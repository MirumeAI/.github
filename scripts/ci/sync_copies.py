#!/usr/bin/env python3
"""写しを持つ Repository へ、 正本（この Repository）の共通のファイルを合わせる
Pull Request を、 まとめて作る。

既定（`plan`）は**計画を表示するだけ**で、 clone と fetch のほかは何も変えない。

    python3 scripts/ci/sync_copies.py plan
    python3 scripts/ci/sync_copies.py apply --source <正本の Pull Request の番号>

なぜ 1 本にするのか。 正本のガード・検査の道具・共通の command を変えるたびに、
写しを持つ 6 つの Repository へ、 Issue・Branch・写しの置き換え・`CLAUDE.md` の
件数の行・Required Verification・Draft の Pull Request を、 その都度書いた script で
作っていた（#93・#95・#97 の同期）。 顧客 Repository の名前を出さない扱い・途中で
止まったときの続き・本文の形を毎回作り直していて、 漏れが起きやすかった。

`apply` が Repository ごとに行うこと（済んだものは飛ばす。 止まったら、 原因を
直して同じ命令で続きから）:

  1. clone（済んでいれば fetch）し、 写し（`copy_sync_check.SHARED`）と
     `CLAUDE.md` の件数の行を、 正本の main と比べる。 差が無ければ何もしない
  2. Feature の Issue を作る（本文は正本の Issue Form の道具で確かめる）
  3. 作業ツリーに `feature/<Issue 番号>-sync-copies` を作り、 写しを正本の中身に
     置き換え、 件数の行を直して commit する（main が進んでいれば取り込む）
  4. Required Verification（`run_required_verification.py`）を流す
  5. 作業 Branch を push し、 Draft の Pull Request を作る（head が変わったら、
     本文の記録の行を直す）

**merge はしない。** Reviewer A・B と Merge の門は、 Pull Request ごとに通す。

守ること:

  ・顧客 Repository の名前を出力に出さない（「顧客 Repository 1」と呼ぶ）。
    git・gh・検査の出力はそのまま出さず、 状態の置き場の `runs/<…>/logs/` に
    書く。 **記録と本文のファイルには名前が残るので、 そのまま表示しない**
  ・作業 Branch（`feature/<数>-…`）にだけ push する。 main へは push しない。
    force push しない
  ・正本の履歴に無い中身の写し（手で直したもの）は、 置き換えずに止まる
    （`--overwrite-unknown` で置き換え、 本文に書く）
  ・`.ci-copy-allow` に書かれた写しは触らない
  ・正本の main に入っていない Pull Request（`--source`）からは同期しない

状態の置き場は `~/.cache/mirumeai/sync-copies/`（`--state-dir` で変える）。
clone は Repository の別名の道（`clones/c1` など）に置く。

このリポジトリは **Public**。 顧客名・社内 URL・IP アドレスをここに書かない。
Repository の名前は実行時に Organization の一覧から読む。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import copy_sync_check                                     # noqa: E402

ORG = "MirumeAI"
CANONICAL = ".github"
REMOTE_BASE = f"https://github.com/{ORG}"
SHARED = copy_sync_check.SHARED
ALLOW_FILE = copy_sync_check.ALLOW_FILE
GUARD_TEST = "scripts/ci/test_guard_main.sh"
DEFAULT_STATE = Path.home() / ".cache" / "mirumeai" / "sync-copies"
DEFAULT_SUBJECT = "共通のファイルの写しを正本に合わせる"
RETRY_SLEEP = 8

#: 名前を出してよい Repository（顧客の名前ではない）と、 その別名。
#: **ここに無い Repository は名前を伏せる**（`customer-` で始まるものは顧客 Repository）。
KNOWN = {"development-docs": "dd", ".github-private": "private",
         "PDM": "pdm", "Image-Acquisition-PDM": "ia"}

#: `CLAUDE.md` の件数の行。 `test_guard_main.sh` と同じく、 最初の「<数> ケース」を見る。
CASES = re.compile(r"([0-9]+)( *ケース)")

#: 件数を書いたほかの行（Repository ごと）。 `CLAUDE.md` と同じ数にする。
EXTRA_COUNT_LINES = {
    "development-docs": (
        ("00_management/current_github_operation.md",
         re.compile(r"(\| `guard-main\.sh` の回帰テスト \| )([0-9]+)( ケース)")),
    ),
}

#: Pull Request の雛形の見出し。 **雛形が変わったら止まる**（本文の形を雛形に合わせ直す）。
PR_HEADINGS = ("関連Issue", "変更目的", "変更内容", "確認結果", "影響範囲",
               "顧客差分・Coreへの影響（該当する場合）", "Reviewerに確認してほしい点")

WORK_BRANCH = re.compile(r"^feature/[0-9]+-[a-z0-9][a-z0-9-]*$")
RECORD = "GitHub Actions unavailable — canonical verification PASS"


class Stop(Exception):
    """その Repository の作業を止める。 理由は名前を伏せて出す。"""


class Fatal(Exception):
    """全体を止める（正本・雛形・引数の問題）。"""


# ---------------------------------------------------------------- 出力と実行
class Masker:
    """伏せる名前を、 別名に置き換える。 出力はすべてここを通す。"""

    def __init__(self) -> None:
        self.pairs: list = []

    def add(self, name: str, label: str) -> None:
        self.pairs.append((name, label))
        self.pairs.sort(key=lambda p: -len(p[0]))

    def __call__(self, text: str) -> str:
        for name, label in self.pairs:
            for form in (f"{ORG}/{name}", name, name.lower()):
                text = text.replace(form, label)
        return text


@dataclass
class Result:
    rc: int
    out: "str | bytes"
    err: str


class Runner:
    """外の命令を流し、 出力を記録のファイルにだけ書く（画面には出さない）。"""

    def __init__(self, log: Path, sleep) -> None:
        self.log = log
        self.sleep = sleep

    def __call__(self, args, *, cwd=None, check=True, retries=1, binary=False,
                 env=None, what="") -> Result:
        args = [str(a) for a in args]
        rc, out, err = 1, b"", b""
        for attempt in range(retries):
            try:
                r = subprocess.run(args, cwd=cwd, capture_output=True, env=env)
                rc, out, err = r.returncode, r.stdout, r.stderr
            except OSError as e:
                rc, out, err = 127, b"", str(e).encode()
            self._write(args, rc, b"" if binary else out, err)
            if rc == 0:
                break
            if attempt + 1 < retries:
                self.sleep(RETRY_SLEEP)
        if check and rc != 0:
            raise Stop(f"{what or args[0]} が失敗しました（終了コード {rc}。記録: {self.log}）")
        return Result(rc, out if binary else out.decode("utf-8", "replace"),
                      err.decode("utf-8", "replace"))

    def _write(self, args, rc, out: bytes, err: bytes) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("ab") as f:
            f.write(("$ " + " ".join(args) + f"  → {rc}\n").encode())
            f.write(out[-20000:] + err[-20000:] + b"\n")


# ---------------------------------------------------------------- 正本
class Canonical:
    def __init__(self, root: Path, run: Runner) -> None:
        self.root, self.run = root, run
        run(["git", "-C", root, "fetch", "-q", "origin"], retries=3, what="正本の fetch")
        self.commit = run(["git", "-C", root, "rev-parse", "origin/main"]).out.strip()

    def blob(self, rel: str) -> "bytes | None":
        r = self.run(["git", "-C", self.root, "show", f"{self.commit}:{rel}"], check=False, binary=True)
        return r.out if r.rc == 0 else None

    def known(self, oid: str) -> bool:
        """正本の履歴に、 この中身が一度でもあったか（前の版の写しか）。"""
        r = self.run(["git", "-C", self.root, "log", "--all", "-1", "--format=%H",
                      f"--find-object={oid}"], check=False)
        return r.rc == 0 and bool(r.out.strip())

    def contains(self, oid: str) -> bool:
        return self.run(["git", "-C", self.root, "merge-base", "--is-ancestor", oid, self.commit],
                        check=False).rc == 0


def guard_cases(canon: Canonical, run: Runner, given: "int | None") -> int:
    """正本の main のガードの回帰テストを流して、 件数を数える。"""
    if given:
        return given
    with tempfile.TemporaryDirectory() as td:
        tree = Path(td)
        data = run(["git", "-C", canon.root, "archive", canon.commit], binary=True).out
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(tree, filter="data")
            else:                                          # 古い Python（正本の git archive なので中身は決まっている）
                tf.extractall(tree)                        # noqa: S202
        # CI_ROOT は、 ガードを探す場所と、 件数を照らす CLAUDE.md の場所（正本の根の CLAUDE.md は件数を書かない）
        r = run(["bash", tree / GUARD_TEST], check=False, env=dict(os.environ, CI_ROOT=str(tree)))
        m = re.search(r"合格 ([0-9]+) / 不合格 ([0-9]+)", r.out)
        if r.rc != 0 or not m or m.group(2) != "0":
            raise Fatal(f"正本のガードの回帰テストが通りません（記録: {run.log}）")
        return int(m.group(1))


def check_templates(canon: Canonical) -> None:
    raw = canon.blob(".github/PULL_REQUEST_TEMPLATE.md")
    heads = tuple(re.findall(r"^## (.+?)\s*$", (raw or b"").decode("utf-8", "replace"), re.M))
    if heads != PR_HEADINGS:
        raise Fatal("Pull Request の雛形の見出しが、 この道具の本文と違います。"
                    " 道具の本文（pr_body）を雛形に合わせてください")


def check_source(gh, canon: Canonical, source: int) -> None:
    r = gh(["pr", "view", source, "-R", f"{ORG}/{CANONICAL}", "--json", "state,mergeCommit"],
           retries=3, what="正本の Pull Request の確かめ")
    info = json.loads(r.out or "{}")
    oid = (info.get("mergeCommit") or {}).get("oid", "")
    if info.get("state") != "MERGED" or not oid or not canon.contains(oid):
        raise Fatal(f"{ORG}/{CANONICAL}#{source} が正本の main（{canon.commit[:12]}）に入っていません")


# ---------------------------------------------------------------- 対象の Repository
@dataclass
class Target:
    name: str
    alias: str
    label: str
    clone: Path


def discover(gh, state_dir: Path, mask: Masker) -> list:
    r = gh(["repo", "list", ORG, "--limit", "500", "--json", "name,isArchived"], retries=3,
           what="Repository の一覧")
    names = sorted(x["name"] for x in json.loads(r.out or "[]")
                   if not x.get("isArchived") and x["name"] != CANONICAL)
    # 別名は一度付けたら変えない（clone の道が別名なので、 並びが変わっても取り違えない）
    amap_file = state_dir / "aliases.json"
    amap = json.loads(amap_file.read_text(encoding="utf-8")) if amap_file.is_file() else {}
    out = []
    for name in names:
        if name in KNOWN:
            alias, label = KNOWN[name], name
        else:
            kind = "c" if name.startswith("customer-") else "o"
            if name not in amap:
                used = {int(a[1:]) for a in amap.values() if a.startswith(kind)}
                amap[name] = f"{kind}{max(used, default=0) + 1}"
            alias = amap[name]
            label = (f"顧客 Repository {alias[1:]}" if kind == "c"
                     else f"そのほかの Repository {alias[1:]}")
            mask.add(name, label)
        out.append(Target(name, alias, label, state_dir / "clones" / alias))
    state_dir.mkdir(parents=True, exist_ok=True)
    amap_file.write_text(json.dumps(amap, ensure_ascii=False, indent=1), encoding="utf-8")
    order = list(KNOWN.values())
    return sorted(out, key=lambda t: (order.index(t.alias) if t.alias in order else len(order),
                                      t.alias[0], int(t.alias[1:]) if t.alias[1:].isdigit() else 0))


def ensure_clone(t: Target, run: Runner, remote_base: str) -> None:
    url = f"{remote_base}/{t.name}.git"
    if not (t.clone / ".git").exists():
        t.clone.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "-q", url, t.clone], retries=3, what="clone")
    for kind in ([], ["--push"]):
        got = run(["git", "-C", t.clone, "remote", "get-url", *kind, "origin"]).out.strip()
        if got != url:
            raise Stop(f"clone の origin が違います（{t.clone}）。 消してから流し直してください")
    run(["git", "-C", t.clone, "fetch", "-q", "--prune", "origin"], retries=3, what="fetch")


def blob(run: Runner, repo: Path, ref: str, rel: str) -> "bytes | None":
    r = run(["git", "-C", repo, "show", f"{ref}:{rel}"], check=False, binary=True)
    return r.out if r.rc == 0 else None


def allowed(raw: "bytes | None") -> set:
    """`.ci-copy-allow`（`copy_sync_check` と同じ読み方。 `#` 以降は理由）。"""
    out = set()
    for ln in (raw or b"").decode("utf-8", "replace").splitlines():
        p = ln.split("#")[0].strip()
        if p:
            out.add(p)
    return out


def _text(raw: bytes) -> "tuple[str, bool]":
    return raw.decode("utf-8-sig"), raw.startswith(b"\xef\xbb\xbf")


@dataclass
class Analysis:
    main: str
    files: list = field(default_factory=list)       # (道, 状態)。 same・old・unknown・allow
    counts: list = field(default_factory=list)      # (道, 今の数, 合わせる数)

    def changes(self) -> list:
        return [rel for rel, st in self.files if st in ("old", "unknown")]

    def count_changes(self) -> list:
        return [c for c in self.counts if c[1] != c[2]]

    def needed(self) -> bool:
        return bool(self.changes() or self.count_changes())


def analyze(t: Target, canon: Canonical, cases: int, run: Runner) -> Analysis:
    main = run(["git", "-C", t.clone, "rev-parse", "origin/main"]).out.strip()
    a = Analysis(main)
    allow = allowed(blob(run, t.clone, main, ALLOW_FILE))
    for rel in SHARED:
        cur = blob(run, t.clone, main, rel)
        if cur is None:
            continue                                       # 持っていない写し
        if rel in allow:
            a.files.append((rel, "allow"))
            continue
        want = canon.blob(rel)
        if want is None:
            raise Stop(f"正本に無い写しがあります: {rel}")
        if cur == want:
            a.files.append((rel, "same"))
            continue
        oid = run(["git", "-C", t.clone, "rev-parse", f"{main}:{rel}"]).out.strip()
        a.files.append((rel, "old" if canon.known(oid) else "unknown"))
    held = dict(a.files)
    if held.get(GUARD_TEST) in ("same", "old", "unknown"):
        lines = [("CLAUDE.md", CASES, 1)] + [(rel, pat, 2) for rel, pat in EXTRA_COUNT_LINES.get(t.name, ())]
        for rel, pat, grp in lines:
            raw = blob(run, t.clone, main, rel)
            m = pat.search(_text(raw)[0]) if raw is not None else None
            if m:
                a.counts.append((rel, int(m.group(grp)), cases))
            elif rel != "CLAUDE.md":
                raise Stop(f"{rel} の件数の行が見つかりません")
    return a


def describe(a: Analysis) -> str:
    n = {s: [r for r, st in a.files if st == s] for s in ("same", "old", "unknown", "allow")}
    parts = [f"同じ {len(n['same'])}"]
    if n["old"]:
        parts.append(f"前の正本 {len(n['old'])}（{'・'.join(n['old'])}）")
    if n["unknown"]:
        parts.append(f"正本の履歴に無い中身 {len(n['unknown'])}（{'・'.join(n['unknown'])}）")
    if n["allow"]:
        parts.append(f"対象外 {len(n['allow'])}")
    for rel, now, want in a.counts:
        parts.append(f"{rel} の件数 {now}" + (f" → {want}" if now != want else ""))
    head = "同期が要る" if a.needed() else "同期は要らない"
    return f"{head} — " + "・".join(parts) if a.files else "写しを持っていない"


# ---------------------------------------------------------------- 本文
def issue_title(subject: str, source: int) -> str:
    return f"[Feature] {subject}（{ORG}/{CANONICAL}#{source}）"


def _list(paths) -> str:
    return ", ".join(f"`{p}`" for p in paths)


def issue_body(a: Analysis, canon: Canonical, source: int, cases: int) -> str:
    ref = f"`{ORG}/{CANONICAL}` の main {canon.commit[:12]}。`{ORG}/{CANONICAL}#{source}`"
    change = [f"- 写し（{_list(a.changes())}）を正本の中身に置き換える"] if a.changes() else []
    change += [f"- `{rel}` の回帰テストの件数の行を {cases} にする" for rel, _, _ in a.count_changes()]
    return "\n".join([
        "### 目的", "", f"共通のファイルの写しを、正本（{ref}）に合わせる。", "",
        "### 背景・課題", "",
        "- 正本が変わり、この Repository の写しが正本とずれている。ずれたままだと、共通の検査（写しの照合）が止める", "",
        "### 変更内容", "", *change, "",
        "### 完了条件", "", "- 写しが正本とバイトで同じになり、Required Verification が PASS する", "",
        "### 担当領域", "", "Infrastructure", "",
        "### 補足", "", f"`{ORG}/{CANONICAL}` の `scripts/ci/sync_copies.py` で作った。", "",
    ])


def pr_title(subject: str, source: int) -> str:
    return f"{subject}（{ORG}/{CANONICAL}#{source}）"


def pr_body(issue: int, a: Analysis, canon: Canonical, source: int, cases: int,
            rv_line: str, overwritten: list, footer: str) -> str:
    ref = f"`{ORG}/{CANONICAL}` の main {canon.commit[:12]}。`{ORG}/{CANONICAL}#{source}`"
    change = ([f"- この Repository が持つ写し（{_list(a.changes())}）を正本の中身に置き換えた（バイトで同じ）"]
              if a.changes() else [])
    change += [f"- `{rel}` の回帰テストの件数の行を {cases} にした" for rel, _, _ in a.count_changes()]
    if overwritten:
        change.append(f"- **正本の履歴に無い中身を置き換えた**: {_list(overwritten)}（`--overwrite-unknown`）")
    body = [
        "## 関連Issue", "", f"Closes #{issue}", "",
        "## 変更目的", "", f"共通のファイルの写しを、正本（{ref}）に合わせる。", "",
        "## 変更内容", "", *change, "",
        "## 確認結果", "",
        "- [x] Issueの完了条件を満たしている",
        "- [x] 変更箇所の動作確認を実施した（写しが正本とバイトで同じ・ほかのファイルは変えていない。"
        "`scripts/ci/sync_copies.py` が確かめた）",
        "- [x] 既存機能への影響を確認した",
        "- [x] 必要なテストが通っている",
        "- [x] Required Verification（正本の検査）が PASS した（記録の行を書く）", "",
        rv_line, "",
        "## 影響範囲", "", "この Repository の写し（ガード・検査の道具・共通の command）。", "",
        "## 顧客差分・Coreへの影響（該当する場合）", "",
        "- 対象Component: 対象外（共通の道具の写し）",
        "- 分類: 対象外",
        "- Coreへの還元: なし",
        "- Core Versionへの影響: なし",
        f"- 関連Issue・PR・Release: `{ORG}/{CANONICAL}#{source}`",
        "- Architecture Review: なし", "",
        "## Reviewerに確認してほしい点", "",
        "- 正本の中身をそのまま写す同期の Pull Request か（差が写しと件数の行だけ・中身が正本と同じ・"
        f"写しの道が `{ALLOW_FILE}` に無い・正本の Pull Request の門の記録がある）", "",
    ]
    if footer:
        body += [footer, ""]
    return "\n".join(body)


# ---------------------------------------------------------------- 1 つの Repository を進める
def check_work_branch(br: str) -> None:
    if not WORK_BRANCH.match(br):
        raise Stop(f"作業 Branch の形（feature/<数>-…）でない Branch には push しません: {br}")


@dataclass
class Ctx:
    run: Runner
    gh: object
    canon: Canonical
    cases: int
    source: int
    subject: str
    trailers: list
    footer: str
    overwrite_unknown: bool
    run_dir: Path
    state: dict
    rv: object
    sleep: object

    def save(self) -> None:
        f = self.run_dir / "state.json"
        f.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")


def _write_body(ctx: Ctx, name: str, text: str) -> Path:
    p = ctx.run_dir / "bodies" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def ensure_issue(ctx: Ctx, t: Target, a: Analysis) -> int:
    repo = f"{ORG}/{t.name}"
    title = issue_title(ctx.subject, ctx.source)
    body = _write_body(ctx, f"{t.alias}-issue.md", issue_body(a, ctx.canon, ctx.source, ctx.cases))
    r = ctx.run(["bash", ctx.canon.root / ".claude" / "scripts" / "issue-form.sh", "check", "feature", body],
                check=False)
    if r.rc != 0 or not r.out.startswith("OK"):
        raise Fatal(f"Issue の本文が正本の Issue Form に合いません（記録: {ctx.run.log}）。 道具の本文を直してください")
    for _ in range(3):
        # 作った直後に止まったときに二重に作らないよう、 同じ題の開いた Issue を先に探す
        r = ctx.gh(["issue", "list", "-R", repo, "--state", "open", "--limit", "100", "--json", "number,title"],
                   retries=3, what="Issue の一覧")
        for it in json.loads(r.out or "[]"):
            if it.get("title") == title:
                return int(it["number"])
        r = ctx.gh(["issue", "create", "-R", repo, "--type", "Feature", "--title", title, "--body-file", body],
                   check=False)
        m = re.search(r"/issues/([0-9]+)\s*$", str(r.out))
        if r.rc == 0 and m:
            return int(m.group(1))
        ctx.sleep(RETRY_SLEEP)
    raise Stop(f"Issue を作れませんでした（記録: {ctx.run.log}）")


def ensure_worktree(ctx: Ctx, t: Target, br: str) -> Path:
    run = ctx.run
    wt = ctx.run_dir / "wt" / t.alias
    if not wt.exists():
        run(["git", "-C", t.clone, "worktree", "prune"])
        has = run(["git", "-C", t.clone, "rev-parse", "--verify", "-q", f"refs/heads/{br}"], check=False).rc == 0
        args = [wt, br] if has else ["--no-track", "-b", br, wt, "origin/main"]
        run(["git", "-C", t.clone, "worktree", "add", "-q", *args], what="作業ツリーの作成")
    cur = run(["git", "-C", wt, "symbolic-ref", "--short", "HEAD"], check=False).out.strip()
    if cur != br:
        raise Stop(f"作業ツリー（{wt}）の Branch が {br} ではありません")
    if run(["git", "-C", wt, "status", "--porcelain"]).out.strip():
        raise Stop(f"作業ツリー（{wt}）に commit していない変更があります")
    return wt


def bring_main(run: Runner, wt: Path) -> None:
    """main が進んでいれば取り込む（履歴は書き換えない）。"""
    if run(["git", "-C", wt, "merge-base", "--is-ancestor", "origin/main", "HEAD"], check=False).rc == 0:
        return
    r = run(["git", "-C", wt, "merge", "-q", "--no-edit", "origin/main"], check=False)
    if r.rc != 0:
        run(["git", "-C", wt, "merge", "--abort"], check=False)
        raise Stop(f"main の取り込みで衝突しました（作業ツリー: {wt}）。 手で取り込んでから流し直してください")


def write_changes(ctx: Ctx, t: Target, a: Analysis, wt: Path) -> list:
    """写しを正本の中身にし、 件数の行を直す。 変えた道を返す。"""
    touched = []
    for rel in a.changes():
        want = ctx.canon.blob(rel)
        p = wt / rel
        if p.read_bytes() != want:
            p.write_bytes(want)
            touched.append(rel)
    pats = {"CLAUDE.md": (CASES, 1), **{rel: (pat, 2) for rel, pat in EXTRA_COUNT_LINES.get(t.name, ())}}
    for rel, _, want in a.counts:
        pat, grp = pats[rel]
        p = wt / rel
        text, bom = _text(p.read_bytes())
        m = pat.search(text)
        if m and int(m.group(grp)) != want:
            text = text[:m.start(grp)] + str(want) + text[m.end(grp):]
            p.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
            touched.append(rel)
    return touched


def run_rv(ctx: Ctx, t: Target, wt: Path, head: str) -> "tuple[str, str]":
    log = ctx.run_dir / "logs" / f"{t.alias}-rv-{head[:12]}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    rc, text = ctx.rv(wt, head, log)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith(RECORD)]
    if rc != 0 or not lines:
        raise Stop(f"Required Verification が PASS しませんでした（記録: {log}）")
    line = lines[-1]
    if "を含む: はい" not in line:
        raise Stop(f"Required Verification の基準が main を含みません（記録: {log}）")
    m = re.search(r"試験 ([0-9]+) 件・飛ばした試験 ([0-9]+) 件", line)
    summary = f"試験 {m.group(1)} 件・飛ばした {m.group(2)} 件" if m else "件数は記録の行に無い"
    return line, summary


def default_rv(canon_root: Path):
    tool = canon_root / "scripts" / "ci" / "run_required_verification.py"

    def rv(wt: Path, head: str, log: Path) -> "tuple[int, str]":
        with log.open("wb") as f:
            r = subprocess.run([sys.executable, str(tool), str(wt), "--head", head], stdout=f,
                               stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED="1"))
        return r.returncode, log.read_text(encoding="utf-8", errors="replace")
    return rv


def apply_one(ctx: Ctx, t: Target, a: Analysis) -> str:
    run = ctx.run
    st = ctx.state.setdefault("targets", {}).setdefault(t.alias, {})
    if not a.needed() and "issue" not in st:
        return "同期は要らない"
    unknown = [rel for rel, s in a.files if s == "unknown"]
    if unknown and not ctx.overwrite_unknown:
        raise Stop(f"正本の履歴に無い中身の写しがあります（{_list(unknown)}）。 手で直した理由を確かめ、"
                   f" 意図したものなら {ALLOW_FILE} に書くか、 --overwrite-unknown で置き換えてください")
    if "issue" not in st:
        st["issue"] = ensure_issue(ctx, t, a)
        st["branch"] = f"feature/{st['issue']}-sync-copies"
        ctx.save()
    br = st["branch"]
    check_work_branch(br)
    wt = ensure_worktree(ctx, t, br)
    bring_main(run, wt)
    touched = write_changes(ctx, t, a, wt)
    if touched:
        msg = ctx.run_dir / "bodies" / f"{t.alias}-commit.txt"
        msg.write_text(f"{pr_title(ctx.subject, ctx.source)}\n\n"
                       f"- 写しを正本（{ORG}/{CANONICAL} の main {ctx.canon.commit[:12]}）の中身にする\n"
                       + ("\n" + "\n".join(ctx.trailers) + "\n" if ctx.trailers else ""), encoding="utf-8")
        run(["git", "-C", wt, "add", "--", *touched])
        run(["git", "-C", wt, "commit", "-q", "-F", msg], what="commit")
    head = run(["git", "-C", wt, "rev-parse", "HEAD"]).out.strip()
    diff = run(["git", "-C", wt, "diff", "--name-only", "origin/main...HEAD"]).out.split()
    if not diff:
        return f"差が無い（Issue #{st['issue']} は、 確かめてから閉じてください）"
    expected = set(SHARED) | {rel for rel, _, _ in a.counts}
    extra = sorted(set(diff) - expected)
    if extra:
        raise Stop(f"写しと件数の行のほかの差があります（{_list(extra)}）。 作業ツリー {wt} を確かめてください")
    if st.get("rv_head") != head:
        st["rv_line"], st["rv_summary"] = run_rv(ctx, t, wt, head)
        st["rv_head"] = head
        ctx.save()
    remote = run(["git", "-C", wt, "rev-parse", "--verify", "-q", f"refs/remotes/origin/{br}"], check=False).out.strip()
    if remote != head:
        if remote and run(["git", "-C", wt, "merge-base", "--is-ancestor", remote, head], check=False).rc != 0:
            raise Stop(f"push 先の {br} に、 手元に無い commit があります（force push はしません）")
        check_work_branch(br)
        run(["git", "-C", wt, "push", "-q", "-u", "origin", br], retries=3, what="push")
    overwritten = [rel for rel in unknown if rel in diff]
    body = _write_body(ctx, f"{t.alias}-pr.md",
                       pr_body(st["issue"], a, ctx.canon, ctx.source, ctx.cases, st["rv_line"], overwritten, ctx.footer))
    repo = f"{ORG}/{t.name}"
    if "pr" not in st:
        r = ctx.gh(["pr", "list", "-R", repo, "--head", br, "--state", "all", "--json", "number,state"],
                   retries=3, what="Pull Request の一覧")
        found = json.loads(r.out or "[]")
        if any(p.get("state") != "OPEN" for p in found):
            raise Stop(f"{br} の Pull Request は既に閉じています。 流し直さず、 状態を確かめてください")
        if found:
            st["pr"] = int(found[0]["number"])
        else:
            r = ctx.gh(["pr", "create", "-R", repo, "--draft", "--base", "main", "--head", br,
                        "--title", pr_title(ctx.subject, ctx.source), "--body-file", body],
                       retries=1, what="Pull Request の作成")
            m = re.search(r"/pull/([0-9]+)\s*$", str(r.out))
            if not m:
                raise Stop(f"Pull Request の番号を読めません（記録: {run.log}）")
            st["pr"] = int(m.group(1))
        st["pr_head"] = None
        ctx.save()
    if st.get("pr_head") != head:
        ctx.gh(["pr", "edit", st["pr"], "-R", repo, "--body-file", body], retries=3, what="本文の更新")
        st["pr_head"] = head
        ctx.save()
    return (f"Issue #{st['issue']}・Pull Request #{st['pr']}（Draft）・head {head[:12]}・"
            f"Required Verification PASS（{st['rv_summary']}）")


# ---------------------------------------------------------------- 入口
def main(argv=None, *, remote_base: str = REMOTE_BASE, rv=None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(description="写しを持つ Repository へ、 正本の共通のファイルを合わせる Pull Request を作る")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, text in (("plan", "計画を表示する（clone と fetch のほかは変えない）"),
                       ("apply", "Issue・Branch・commit・Required Verification・Draft の Pull Request まで進める")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--state-dir", type=Path, default=DEFAULT_STATE, help="状態と clone の置き場")
        p.add_argument("--only", default="", help="別名をカンマで（例: dd,pdm）。 plan が別名を出す")
        p.add_argument("--canonical", type=Path, default=ROOT, help="正本（MirumeAI/.github）の clone")
        p.add_argument("--guard-cases", type=int, help="ガードの回帰テストの件数（省くと正本で流して数える）")
        p.add_argument("--source", type=int, required=(name == "apply"),
                       help="同期のもとになった正本の Pull Request の番号（merge 済み）")
    ap_apply = sub.choices["apply"]
    ap_apply.add_argument("--subject", default=DEFAULT_SUBJECT, help="Issue と Pull Request の題")
    ap_apply.add_argument("--trailer", action="append", default=[], help="commit の終わりに足す行")
    ap_apply.add_argument("--pr-footer", default="", help="Pull Request の本文の終わりに足す行")
    ap_apply.add_argument("--overwrite-unknown", action="store_true",
                          help="正本の履歴に無い中身の写しも置き換える（本文に書く）")
    args = ap.parse_args(argv)

    mask = Masker()

    def say(msg: str, err: bool = False) -> None:
        print(mask(msg), file=sys.stderr if err else sys.stdout, flush=True)

    state_dir = args.state_dir.expanduser().resolve()
    run_dir = state_dir / "runs" / (f"github-{args.source}" if args.source else "plan")
    run = Runner(run_dir / "logs" / "commands.log", sleep)
    gh = lambda a, **k: run(["gh", *a], **k)                # noqa: E731
    try:
        canon = Canonical(args.canonical.resolve(), run)
        check_templates(canon)
        if args.source:
            check_source(gh, canon, args.source)
        cases = guard_cases(canon, run, args.guard_cases)
        targets = discover(gh, state_dir, mask)
    except (Stop, Fatal) as e:
        say(f"止まりました: {e}", err=True)
        return 2
    only = {x.strip() for x in args.only.split(",") if x.strip()}
    unknown_only = only - {t.alias for t in targets}
    if unknown_only:
        say(f"知らない別名: {', '.join(sorted(unknown_only))}", err=True)
        return 2
    targets = [t for t in targets if not only or t.alias in only]
    say(f"正本: {ORG}/{CANONICAL} の main {canon.commit[:12]}（ガードの回帰テスト {cases} ケース）")

    state_file = run_dir / "state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    if args.cmd == "apply":
        before = state.get("canonical")
        if before and before != canon.commit:
            say(f"正本が進みました（{before[:12]} → {canon.commit[:12]}）。 新しい正本に合わせます")
        state["source"], state["canonical"] = args.source, canon.commit
        run_dir.mkdir(parents=True, exist_ok=True)
    ctx = Ctx(run, gh, canon, cases, args.source or 0, getattr(args, "subject", DEFAULT_SUBJECT),
              getattr(args, "trailer", []), getattr(args, "pr_footer", ""),
              getattr(args, "overwrite_unknown", False), run_dir, state,
              rv or default_rv(canon.root), sleep)
    if args.cmd == "apply":
        ctx.save()

    failed = 0
    for t in targets:
        try:
            ensure_clone(t, run, remote_base)
            a = analyze(t, canon, cases, run)
            msg = describe(a) if args.cmd == "plan" else apply_one(ctx, t, a)
            say(f"{t.alias:<8} {t.label}（main {a.main[:12]}）: {msg}")
        except Stop as e:
            failed += 1
            say(f"{t.alias:<8} {t.label}: 止まりました — {e}", err=True)
        except Fatal as e:
            say(f"止まりました: {e}", err=True)
            return 2
    if args.cmd == "apply":
        say("次: Pull Request ごとに Reviewer A・B と Merge の門を通す（この道具は merge しない）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

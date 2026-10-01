#!/usr/bin/env python3
"""顧客案件の Repository を作る。 **Organization の Owner が実行する。**

既定は**計画を表示するだけ**で、 何も変えない（読み取りで確かめるだけ）。

    python3 scripts/ci/new_customer_repo.py customer-<会社>-<部品>           # 計画
    python3 scripts/ci/new_customer_repo.py customer-<会社>-<部品> --apply   # 作る
    python3 scripts/ci/new_customer_repo.py customer-<会社>-<部品> --check   # 確かめる

なぜ 1 本にするのか。 手で作ると 10 前後の手作業があり、 漏れても
気づきにくい。 2026-10-01 に手順を試すと、 次の状態だった。

  ・雛形の README にある取り出しのコマンドが、 書かれたとおりでは動かない
  ・雛形に Claude Code 一式が無く、 main を守る hook を手で写していた
  ・Team の権限、 Squash だけにする設定、 Dependabot Alerts は、 新しい
    Repository に自動では付かない

`--apply` が行うこと（済んだものは飛ばす）:

  1. Repository を作る（Private）
  2. 分類（Custom Properties）を稼働前の値にする
  3. Merge を Squash だけにし、 merge 後に Branch を消す
  4. Team の権限を付ける
  5. Dependabot Alerts を有効にする
  6. 最初の中身を **Pull Request** にする（main へ直接入れない）

CI が通ったら人が Squash and merge し、 そのあと `--check` を実行する
（読み取りだけ。 この Repository について Organization 監査も行う）。
途中で止まったら、 原因を直して `--apply --resume` で続きから実行する。

**Claude Code からは `--apply` を実行できない。** Repository の設定は人が
変える（development-docs の D5）。 hook は `gh api` の書き込みを止めるが、
この script の中で呼ぶ `gh` は見えないので、 ここでも止める。

このリポジトリは **Public**。 顧客名・社内 URL・IP アドレスをここに書かない。
案件の名前は実行時に受け取る。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import org_audit                                           # noqa: E402

ORG = org_audit.ORG

#: 雛形の場所（この Repository の中）。
TEMPLATE = "templates/customer-project"

#: 雛形の中で、 案件の名前に置き換える文字列。
PLACEHOLDER = "<案件の名前>"

#: `customer-<会社>-<部品>`。 英小文字・数字・ハイフンだけ。
NAME_RE = re.compile(r"^customer-[a-z0-9]+(?:-[a-z0-9]+)+$")

#: 正本から写すもの。 **雛形に複製を置かない。** 置くと正本と雛形の 2 つに
#: なり、 片方だけが直る。 すべて照合の対象（`copy_sync_check.SHARED`）
#: なので、 写した後のずれは新しい Repository の CI が見つける。
FROM_CANONICAL = (
    ".claude/hooks/guard-main.sh",
    ".claude/scripts/issue-form.sh",
    ".claude/commands/branch.md",
    ".claude/commands/customer-request.md",
    ".claude/commands/issue.md",
    ".claude/commands/pr.md",
    "scripts/ci/basic_checks.py",
    "scripts/ci/import_check.py",
    "scripts/ci/test_guard_main.sh",
)

#: 照合の対象のうち、 顧客案件へ写さないもの。 **理由を書く。** 照合の
#: 対象を足したら、 写すか（`FROM_CANONICAL`）ここかを決める（テストが
#: 確かめる）。
NOT_FOR_CUSTOMER = {
    ".claude/commands/docs-sync.md":
        "development-docs と .github を変えたときの手順。 顧客案件では使わない",
    "scripts/ci/customer_data_check.py":
        "顧客案件の config/ には PLC の接続先が正当に入る"
        "（再利用 workflow にも入れていない）",
}

#: `.claude/settings.json` は**正本から作る**。 顧客案件はライセンス
#: ファイル（`*.lic`）の読み書きも拒否する（雛形の CLAUDE.md の 6）。
#: 照合の対象に入れていないのは、 この差が意図したものだから
#: （`copy_sync_check.py`）。 既存の顧客案件と同じ中身になることを
#: 2026-10-01 に確かめた。
SETTINGS = ".claude/settings.json"
SETTINGS_EXTRA_DENY = ("Read(**/*.lic)", "Edit(**/*.lic)")

#: main に無ければならないもの（`--check` が確かめる）。 hook・設定・CI が
#: 無いと、 main を守る壁が無いまま始まる。
MUST_HAVE = ("CLAUDE.md", SETTINGS, ".github/workflows/basic-checks.yml",
             *FROM_CANONICAL)

#: Team と権限（As-Is Inventory の D12）。 既存の顧客案件と同じ。
TEAMS = (("developers", "push"), ("maintainers", "admin"))

#: Repository の設定。 既存の顧客案件と同じ（2026-10-01 実測）。
#: **新しい Repository の既定では merge commit と rebase も通る。**
REPO_SETTINGS = {
    "allow_squash_merge": True,
    "allow_merge_commit": False,
    "allow_rebase_merge": False,
    "delete_branch_on_merge": True,
    "has_wiki": False,
    "has_projects": False,
}

#: 分類（Custom Properties）。 **稼働前の値。** 現場で稼働したら
#: `production_impact` と `governance_profile` を `direct` と `strict` に
#: 変える（雛形の README）。 値が標準にあり、 組が監査の規則に合うことは
#: テストが確かめる。
PROPERTIES = {
    "repo_type": "customer-project",
    "lifecycle": "active",
    "owner_team": "delivery",
    "domain": "visual-inspection",
    "criticality": "high",
    "data_classification": "customer-confidential",
    "production_impact": "none",
    "governance_profile": "standard",
}

#: 最初の中身を入れる Branch。 Issue は作らない（雛形から機械的に作る
#: 中身で、 判断を含まない）。
BRANCH = "feature/initial-setup"
PR_TITLE = "初期設定: 雛形と Claude Code 一式を入れる"

OK, NG, UNKNOWN, NOTE = "OK", "NG", "不明", "注"


class ApiError(Exception):
    """`gh api` が失敗した。 **推測で続けない。**"""


class SourceError(Exception):
    """正本（この Repository の clone）から中身を作れない。"""


def _text(b) -> str:
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")


def _reason(out: str, err: str) -> str:
    """失敗の理由。 応答の message（と細目）と、 gh の最後の行。"""
    parts = []
    try:
        body = json.loads(out)
        parts.append(body.get("message", ""))
        parts += [e.get("message", "") for e in body.get("errors") or []
                  if isinstance(e, dict)]
    except (ValueError, AttributeError):
        pass
    lines = err.strip().splitlines()
    if lines:
        parts.append(lines[-1])
    return " / ".join(p for p in parts if p)[:400] or "理由が分からない"


def _run_gh(args: list, data: "bytes | None"):
    gh = shutil.which("gh")
    if gh is None:
        raise ApiError("gh コマンドがありません（2.94.0 以降を入れてください）")
    return subprocess.run([gh, *args], input=data, capture_output=True)


class Api:
    """`gh api` の薄い包み。

    **書き込みは `send` だけ。** 計画の表示と `--check` は `get` しか
    呼ばない（テストが確かめる）。
    """

    def __init__(self, run=_run_gh) -> None:
        self._run = run

    def _call(self, method: str, path: str, body=None) -> tuple:
        args = ["api", "-X", method, path]
        data = None
        if body is not None:
            args += ["--input", "-"]
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        r = self._run(args, data)
        return r.returncode, _text(r.stdout), _text(r.stderr)

    @staticmethod
    def _json(method: str, path: str, out: str):
        if not out.strip():
            return None                  # 204 No Content
        try:
            return json.loads(out)
        except ValueError:
            raise ApiError(f"{method} {path}: 応答が JSON ではない") from None

    def get(self, path: str) -> tuple:
        """`("ok", 値)` か `("missing", None)`（404）。 ほかの失敗は ApiError。"""
        code, out, err = self._call("GET", path)
        if code == 0:
            return "ok", self._json("GET", path, out)
        if "(HTTP 404)" in err:
            return "missing", None
        raise ApiError(f"GET {path}: {_reason(out, err)}")

    def send(self, method: str, path: str, body=None):
        code, out, err = self._call(method, path, body)
        if code != 0:
            raise ApiError(f"{method} {path}: {_reason(out, err)}")
        return self._json(method, path, out)


class Source:
    """最初の中身の出どころ。 この Repository の clone の commit。

    **作業ツリーではなく commit から読む。** commit していない変更や、
    Windows での改行の変換が混ざると、 照合（byte の比較）で必ず落ちる。
    """

    def __init__(self, root: Path = ROOT, rev: str = "HEAD") -> None:
        self.root, self.rev = Path(root), rev

    def _git(self, *args: str) -> bytes:
        r = subprocess.run(["git", "-C", str(self.root), *args],
                           capture_output=True)
        if r.returncode != 0:
            raise SourceError(f"git {' '.join(args)}: "
                              + _text(r.stderr).strip()[:200])
        return r.stdout

    def commit(self) -> str:
        return self._git("rev-parse", f"{self.rev}^{{commit}}").decode().strip()

    def files(self, *paths: str) -> dict:
        """`{パス: (mode, 中身)}`。 フォルダを渡すと、 中のファイルすべて。"""
        out = {}
        raw = self._git("ls-tree", "-r", "-z", self.rev, "--", *paths)
        for rec in raw.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, kind, sha = meta.decode().split()
            if kind == "blob":
                out[path.decode("utf-8")] = (mode,
                                             self._git("cat-file", "blob", sha))
        return out


@dataclass
class Plan:
    name: str
    title: str
    properties: dict
    commit: str          # 中身を取った正本の commit
    tree: dict           # {パス: (mode, 中身)}
    origin: dict         # {パス: "雛形" | "正本" | "作る"}

    @property
    def description(self) -> str:
        # 既存の顧客案件と同じ書き方
        return f"{self.title} 向けの顧客固有設定・Plugin・Recipe・Deploy設定"

    def count(self, origin: str) -> int:
        return sum(1 for o in self.origin.values() if o == origin)


def check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(f"Repository の名前 {name!r} は customer-<会社>-<部品>"
                         " の形にしてください（英小文字・数字・ハイフン）")


def default_title(name: str) -> str:
    """`customer-example-part` → `example part`（既存の案件と同じ付け方）。"""
    return name[len("customer-"):].replace("-", " ")


def customer_settings(canonical: bytes) -> bytes:
    """顧客案件の `.claude/settings.json`。 正本に `*.lic` の拒否を足す。"""
    d = json.loads(canonical.decode("utf-8"))
    deny = d["permissions"]["deny"]
    for rule in SETTINGS_EXTRA_DENY:
        if rule not in deny:
            deny.append(rule)
    return (json.dumps(d, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def build_tree(src: Source, title: str) -> tuple:
    """最初の中身。 `({パス: (mode, 中身)}, {パス: 出どころ})`。"""
    tree, origin = {}, {}
    for path, (mode, data) in src.files(TEMPLATE).items():
        rel = path[len(TEMPLATE) + 1:]
        data = data.replace(PLACEHOLDER.encode("utf-8"), title.encode("utf-8"))
        tree[rel], origin[rel] = (mode, data), "雛形"
    canon = src.files(*FROM_CANONICAL, SETTINGS)
    for rel in (*FROM_CANONICAL, SETTINGS):
        if rel not in canon:
            raise SourceError(f"正本に {rel} がありません")
        if rel in tree:
            # どちらが正しいか決められない。 黙って片方を選ばない
            raise SourceError(f"{rel} が雛形と正本の両方にあります")
    for rel in FROM_CANONICAL:
        tree[rel], origin[rel] = canon[rel], "正本"
    tree[SETTINGS] = ("100644", customer_settings(canon[SETTINGS][1]))
    origin[SETTINGS] = "作る"
    for rel in ("README.md", "CLAUDE.md"):
        if rel not in tree:
            raise SourceError(f"雛形に {rel} がありません")
    for rel, (_, data) in tree.items():
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            raise SourceError(f"{rel} が UTF-8 のテキストではない"
                              "（API で送れない）") from None
    return tree, origin


def make_plan(name: str, title: "str | None", properties: dict,
              src: Source) -> Plan:
    check_name(name)
    title = (title if title is not None else default_title(name)).strip()
    if not title or "\n" in title or "<" in title or ">" in title:
        raise ValueError("案件の名前は 1 行で、 < と > を含めないでください")
    tree, origin = build_tree(src, title)
    return Plan(name, title, dict(properties), src.commit(), tree, origin)


# --------------------------------------------------------------------------
# 計画（読み取りだけ）


def preflight(api: Api, plan: Plan, resume: bool) -> tuple:
    """読み取りだけで確かめる。 `([(状態, 説明)], 既にある Repository)`。

    **確かめられなかったものは「不明」にする。** 推測で OK にしない。
    """
    res = []

    def add(st, msg):
        res.append((st, msg))

    try:
        _, me = api.get("/user")
        add(OK, f"gh でログインしている（{(me or {}).get('login', '?')}）")
    except ApiError as e:
        add(NG, f"gh でログインしていない: {e}")
        return res, None                 # これより先は確かめられない

    # 照合（byte の比較）は main の正本と比べる。 古い clone から写すと、
    # 最初の Pull Request の CI で必ず落ちる
    try:
        _, ref = api.get(f"/repos/{ORG}/.github/git/ref/heads/main")
        latest = ((ref or {}).get("object") or {}).get("sha") or ""
        if latest == plan.commit:
            add(OK, f"正本の clone が main の最新と同じ（{plan.commit[:7]}）")
        else:
            add(NG, f"正本の clone（{plan.commit[:7]}）が main の最新"
                    f"（{latest[:7] or '?'}）と違う。"
                    " git switch main && git pull で揃えてください")
    except ApiError as e:
        add(UNKNOWN, f"main の最新を確かめられない: {e}")

    repo = None
    try:
        st, got = api.get(f"/repos/{ORG}/{plan.name}")
        if st == "missing":
            add(OK, f"{ORG}/{plan.name} はまだ無い")
        elif resume:
            repo = got
            add(OK, f"{ORG}/{plan.name} は既にある（--resume で続きから行う）")
        else:
            repo = got
            add(NG, f"{ORG}/{plan.name} は既にある。 前回の --apply が途中で"
                    "止まったときだけ --resume を付けてください")
    except ApiError as e:
        add(UNKNOWN, f"{ORG}/{plan.name} の有無を確かめられない: {e}")

    for slug, _ in TEAMS:
        try:
            st, _ = api.get(f"/orgs/{ORG}/teams/{slug}")
            add(OK if st == "ok" else NG,
                f"Team {slug} が" + ("ある" if st == "ok" else "無い"))
        except ApiError as e:
            add(UNKNOWN, f"Team {slug} を確かめられない: {e}")

    try:
        _, schema = api.get(f"/orgs/{ORG}/properties/schema")
        defined = {p["property_name"]: p for p in schema or []}
        bad = []
        for key, value in plan.properties.items():
            p = defined.get(key)
            if p is None:
                bad.append(f"{key} が Organization に定義されていない")
            elif p.get("allowed_values") and value not in p["allowed_values"]:
                bad.append(f"{key}={value} が選べる値に無い")
        add(NG if bad else OK, "分類の値が Organization の定義にある"
            if not bad else "分類: " + "、 ".join(bad))
    except ApiError as e:
        add(UNKNOWN, f"分類の定義を確かめられない: {e}")
    return res, repo


def _show_results(results: list) -> None:
    for st, msg in results:
        print(f"  {st:4}  {msg}")


def _properties_line(props: dict) -> str:
    return " ".join(f"{k}={v}" for k, v in props.items())


def show_plan(plan: Plan) -> None:
    print("--apply で行うこと")
    print("  1. Repository を作る（Private）")
    print("  2. 分類（Custom Properties）を稼働前の値にする")
    print(f"       {_properties_line(plan.properties)}")
    print("  3. Merge を Squash だけにし、 merge 後に Branch を消す")
    print("  4. Team の権限: "
          + ", ".join(f"{s}={p}" for s, p in TEAMS))
    print("  5. Dependabot Alerts を有効にする")
    print(f"  6. 最初の中身を Pull Request にする（Branch {BRANCH}。"
          " main へ直接入れない）")
    print()
    print(f"最初の中身（{len(plan.tree)} ファイル）")
    for kind, note in (("雛形", f"{TEMPLATE}/ から。 案件の名前を入れる"),
                       ("正本", "この Repository から写す（照合の対象）"),
                       ("作る", f"正本に {' と '.join(SETTINGS_EXTRA_DENY)}"
                                " の拒否を足す")):
        paths = sorted(p for p, o in plan.origin.items() if o == kind)
        print(f"  {kind}  {len(paths)} 件（{note}）")
        for p in paths:
            print(f"        {p}")


# --------------------------------------------------------------------------
# 作る


def properties_to_set(api: Api, plan: Plan, resume: bool) -> tuple:
    """入れる値と、 変えずに残す値。

    **続きから行うときは、 入っている値を上書きしない**（空と既定値だけを
    埋める）。 前回の後に人が直した値を、 黙って戻さないため。
    """
    if not resume:
        return dict(plan.properties), {}
    _, now = api.get(f"/repos/{ORG}/{plan.name}/properties/values")
    current = {p["property_name"]: p.get("value") for p in now or []}
    _, schema = api.get(f"/orgs/{ORG}/properties/schema")
    defaults = {p["property_name"]: p.get("default_value")
                for p in schema or []}
    todo, kept = {}, {}
    for key, value in plan.properties.items():
        cur = current.get(key)
        if cur == value:
            continue
        if cur in (None, "") or cur == defaults.get(key):
            todo[key] = value
        else:
            kept[key] = cur
    return todo, kept


def _wait_for_branch(api: Api, name: str, base: str, sleep,
                     tries: int = 10) -> str:
    """作った直後は、 最初の commit がまだ見えないことがある。"""
    last = "404"
    for _ in range(tries):
        try:
            st, ref = api.get(f"/repos/{ORG}/{name}/git/ref/heads/{base}")
            if st == "ok":
                return ref["object"]["sha"]
        except ApiError as e:
            last = str(e)
        sleep(2)
    raise ApiError(f"{base} の最初の commit が見えない（{last}）")


def commit_message(plan: Plan) -> str:
    return (f"{PR_TITLE}\n\n"
            f"MirumeAI/.github の {plan.commit[:12]} から、"
            " scripts/ci/new_customer_repo.py で作った。\n")


def pr_body(plan: Plan) -> str:
    return f"""## 関連Issue

なし。雛形から機械的に作る初期設定で、判断を含まないため Issue を作っていない。

## 変更目的

顧客案件の雛形と Claude Code 一式を入れ、標準のフロー（Issue → Branch → Pull Request → CI）で開発を始められるようにする。

## 変更内容

`MirumeAI/.github` の `{plan.commit[:12]}` から、`scripts/ci/new_customer_repo.py` で作った。

- 雛形（`{TEMPLATE}/`）{plan.count("雛形")} ファイル。案件の名前は「{plan.title}」
- Claude Code 一式と、手元で実行する検査 {plan.count("正本")} ファイル。正本から写した（照合の対象なので、ずれると CI が止まる）
- `{SETTINGS}`。正本に `*.lic` の拒否を足して作った

## 確認結果

- [ ] CI（basic-checks）が通っている
- [ ] merge した後に `python3 scripts/ci/new_customer_repo.py {plan.name} --check` を実行し、NG が無い

## 影響範囲

この Repository だけ。

## 顧客差分・Coreへの影響（該当する場合）

- 対象Component: Customer only
- 分類: 対象外

## Reviewerに確認してほしい点

案件の名前（「{plan.title}」）と、`README.md` の「始めるときにやること」。
"""


def open_pull_request(api: Api, plan: Plan, base: str, sleep) -> tuple:
    """最初の中身を Pull Request にする。 `(説明, URL)`。

    **main へ直接入れない。** Branch を作って Pull Request にし、 CI を
    通してから人が merge する。
    """
    name = plan.name
    st, _ = api.get(f"/repos/{ORG}/{name}/contents/CLAUDE.md?ref={base}")
    if st == "ok":
        return "最初の中身は既に main にある（飛ばした）", None
    _, prs = api.get(f"/repos/{ORG}/{name}/pulls?state=all&head={ORG}:{BRANCH}")
    if prs:
        pr = prs[0]
        if pr.get("state") == "closed" and not pr.get("merged_at"):
            raise ApiError("最初の Pull Request が merge されずに閉じられている:"
                           f" {pr.get('html_url')}。 開き直してください")
        return "Pull Request は既にある（飛ばした）", pr.get("html_url")
    st, _ = api.get(f"/repos/{ORG}/{name}/git/ref/heads/{BRANCH}")
    if st != "ok":
        head = _wait_for_branch(api, name, base, sleep)
        _, base_commit = api.get(f"/repos/{ORG}/{name}/git/commits/{head}")
        entries = [{"path": p, "mode": m, "type": "blob",
                    "content": d.decode("utf-8")}
                   for p, (m, d) in sorted(plan.tree.items())]
        tree = api.send("POST", f"/repos/{ORG}/{name}/git/trees",
                        {"base_tree": base_commit["tree"]["sha"],
                         "tree": entries})
        commit = api.send("POST", f"/repos/{ORG}/{name}/git/commits",
                          {"message": commit_message(plan),
                           "tree": tree["sha"], "parents": [head]})
        api.send("POST", f"/repos/{ORG}/{name}/git/refs",
                 {"ref": f"refs/heads/{BRANCH}", "sha": commit["sha"]})
    pr = api.send("POST", f"/repos/{ORG}/{name}/pulls",
                  {"title": PR_TITLE, "head": BRANCH, "base": base,
                   "body": pr_body(plan)})
    return "Pull Request を作った", pr.get("html_url")


def apply(api: Api, plan: Plan, repo, resume: bool, sleep=time.sleep) -> int:
    """作る。 **済んだものは飛ばす**（途中で止まっても続きから行える）。"""
    name = plan.name
    state = {"repo": repo, "url": None}

    def create():
        if state["repo"] is not None:
            return "既にある（飛ばした）"
        state["repo"] = api.send("POST", f"/orgs/{ORG}/repos", {
            "name": name,
            "description": plan.description,
            "private": True,
            "has_issues": True,
            "has_wiki": False,
            "has_projects": False,
            # 最初の commit が無いと Branch も Pull Request も作れない
            "auto_init": True,
        })
        return "作った（Private）"

    def classify():
        todo, kept = properties_to_set(api, plan, resume)
        if todo:
            api.send("PATCH", f"/repos/{ORG}/{name}/properties/values",
                     {"properties": [{"property_name": k, "value": v}
                                     for k, v in todo.items()]})
        msg = f"{len(todo)} 件を入れた" if todo else "入っている（飛ばした）"
        if kept:
            msg += "。 入っていた値を変えていない: " + _properties_line(kept)
        return msg

    def merge_settings():
        api.send("PATCH", f"/repos/{ORG}/{name}", dict(REPO_SETTINGS))
        return "Squash だけ、 merge 後に Branch を消す"

    def teams():
        for slug, perm in TEAMS:
            api.send("PUT", f"/orgs/{ORG}/teams/{slug}/repos/{ORG}/{name}",
                     {"permission": perm})
        return ", ".join(f"{s}={p}" for s, p in TEAMS)

    def dependabot():
        api.send("PUT", f"/repos/{ORG}/{name}/vulnerability-alerts")
        return "有効にした"

    def pull_request():
        base = (state["repo"] or {}).get("default_branch") or "main"
        msg, state["url"] = open_pull_request(api, plan, base, sleep)
        return msg

    steps = (("Repository", create), ("分類", classify),
             ("Merge の設定", merge_settings), ("Team の権限", teams),
             ("Dependabot Alerts", dependabot), ("最初の中身", pull_request))
    print("作ります")
    for i, (label, step) in enumerate(steps, 1):
        try:
            msg = step()
        except ApiError as e:
            print(f"  {i}/{len(steps)}  NG  {label}: {e}")
            print("\n止まりました。 原因を直してから、 --apply --resume を付けて"
                  "続きから実行してください。 済んだ手順は飛ばします。")
            return 1
        print(f"  {i}/{len(steps)}  OK  {label}: {msg}")
    print()
    if state["url"]:
        print(f"Pull Request: {state['url']}")
    print("CI が通ったら Squash and merge し、 次を実行してください"
          "（読み取りだけ）:")
    print(f"    python3 scripts/ci/new_customer_repo.py {name} --check")
    return 0


# --------------------------------------------------------------------------
# 確かめる（読み取りだけ）


class _AuditGh:
    """`org_audit.Gh` と同じ形（`get` と `unknown`）。 **読み取りだけ。**"""

    def __init__(self, api: Api) -> None:
        self.api, self.unknown = api, []

    def get(self, path: str, why: str):
        try:
            st, data = self.api.get(path)
        except ApiError as e:
            # Plan の制約は「機能が無い」という確定事実（org_audit と同じ）
            if org_audit.Gh._PLAN_MSG not in str(e):
                self.unknown.append(f"{why}: {e}")
            return None
        if st == "missing":
            self.unknown.append(f"{why}: Not Found (HTTP 404)")
            return None
        return data


def audit_one(api: Api, name: str) -> tuple:
    """Organization 監査のうち、 この Repository の分。

    `(所見, Exception, 不明, 監査の一覧にあったか)`。 判定は
    `org_audit.py` そのものを使う（判定を 2 つにしない）。
    """
    gh = _AuditGh(api)
    org = org_audit.audit_org(gh)
    repos = org_audit.audit_repos(gh)
    local = org_audit.collect_local(ROOT, repos, gh)
    findings, exceptions = org_audit.classify(org, repos, local)
    return ([f for f in findings if f["repo"] == name],
            [e for e in exceptions if e["repo"] == name],
            [u for u in gh.unknown if u.startswith(f"{name} ")],
            name in repos)


def check(api: Api, plan: Plan, audit=audit_one) -> int:
    """作った結果を確かめる。 **書き込まない。**"""
    name = plan.name
    print(f"{ORG}/{name} を確かめます（読み取りだけ）\n")
    st, repo = api.get(f"/repos/{ORG}/{name}")
    if st == "missing":
        print(f"  {NG:4}  {ORG}/{name} が無い")
        return 1
    res = []

    def add(st, msg):
        res.append((st, msg))

    add(OK if repo.get("private") else NG,
        "Private" if repo.get("private") else "Private ではない")
    wrong = [f"{k}={repo.get(k)}" for k, v in REPO_SETTINGS.items()
             if repo.get(k) != v]
    add(NG if wrong else OK, "Merge は Squash だけ、 merge 後に Branch を消す"
        + (f"（違う: {', '.join(wrong)}）" if wrong else ""))

    _, teams = api.get(f"/repos/{ORG}/{name}/teams")
    have = {t["slug"]: t.get("permission") for t in teams or []}
    wrong = [f"{s}={have.get(s, 'なし')}" for s, p in TEAMS if have.get(s) != p]
    add(NG if wrong else OK, "Team の権限: "
        + ", ".join(f"{s}={p}" for s, p in TEAMS)
        + (f"（違う: {', '.join(wrong)}）" if wrong else ""))

    st, _ = api.get(f"/repos/{ORG}/{name}/vulnerability-alerts")
    add(OK if st == "ok" else NG,
        "Dependabot Alerts が" + ("有効" if st == "ok" else "無効"))

    base = repo.get("default_branch") or "main"
    _, tree = api.get(f"/repos/{ORG}/{name}/git/trees/{base}?recursive=1")
    paths = {e["path"] for e in (tree or {}).get("tree", [])
             if e.get("type") == "blob"}
    missing = [p for p in MUST_HAVE if p not in paths]
    if not missing:
        add(OK, f"Claude Code 一式と CI が {base} にある（{len(MUST_HAVE)} ファイル）")
    elif "CLAUDE.md" in missing:
        add(NG, f"Claude Code 一式と CI が {base} に無い"
                "（最初の Pull Request がまだ merge されていない）")
    else:
        add(NG, f"{base} に無い: {', '.join(missing)}")

    _, now = api.get(f"/repos/{ORG}/{name}/properties/values")
    current = {p["property_name"]: p.get("value") for p in now or []}
    differ = {k: current.get(k) for k, v in plan.properties.items()
              if current.get(k) != v}
    if differ:
        add(NOTE, f"分類が作ったときの値と違う: {_properties_line(differ)}"
                  "（稼働した後に変えた値なら問題ない。 正しさは監査が見る）")

    findings, exceptions, unknown, listed = audit(api, name)
    if not listed:
        add(UNKNOWN, "Organization 監査の一覧に無い（監査できない）")
    for f in findings:
        add(NG, f"監査 [{f['severity']}] {f['message']}")
    for u in unknown:
        add(UNKNOWN, f"監査で確かめられない: {u}")
    if listed and not findings:
        note = (f"（Exception {len(exceptions)} 件: Plan の制約。"
                " 省略ではない）" if exceptions else "")
        add(OK, "Organization 監査の所見なし" + note)

    _show_results(res)
    print()
    bad = [r for r in res if r[0] in (NG, UNKNOWN)]
    print("問題ありません。" if not bad else
          f"NG または不明が {len(bad)} 件あります。")
    return 1 if bad else 0


# --------------------------------------------------------------------------


def main(argv=None, env=None, api: "Api | None" = None,
         src: "Source | None" = None, sleep=time.sleep) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("name", help="Repository の名前（customer-<会社>-<部品>）")
    ap.add_argument("--title", help="案件の名前。 既定は Repository の名前から"
                                    "作る（customer-example-part → example part）")
    ap.add_argument("--domain", choices=org_audit.STANDARD["domain"],
                    default=PROPERTIES["domain"], help="分類の domain")
    ap.add_argument("--criticality", choices=org_audit.STANDARD["criticality"],
                    default=PROPERTIES["criticality"],
                    help="分類の criticality")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="作る（Organization の Owner が実行する）")
    mode.add_argument("--check", action="store_true",
                      help="作った結果を確かめる（読み取りだけ）")
    ap.add_argument("--resume", action="store_true",
                    help="--apply が途中で止まったとき、 続きから行う")
    a = ap.parse_args(argv)
    env = os.environ if env is None else env

    if a.resume and not a.apply:
        ap.error("--resume は --apply と一緒に使います")
    if a.apply and env.get("CLAUDECODE"):
        print("--apply は Claude Code の中からは実行できません。 Repository の"
              "設定は人が変えます（development-docs の D5）。\n"
              "ターミナルで Organization の Owner が実行してください。",
              file=sys.stderr)
        return 2

    props = dict(PROPERTIES, domain=a.domain, criticality=a.criticality)
    try:
        plan = make_plan(a.name, a.title, props,
                         src if src is not None else Source())
    except (ValueError, SourceError) as e:
        print(f"NG: {e}", file=sys.stderr)
        return 2

    api = api if api is not None else Api()
    try:
        if a.check:
            return check(api, plan)
        results, repo = preflight(api, plan, a.resume)
        if not a.apply:
            print(f"{ORG}/{plan.name} を作る計画です。 まだ何も変えていません。\n")
        print(f"  案件の名前  {plan.title}")
        print(f"  説明        {plan.description}")
        print(f"  中身の出どころ  {ORG}/.github の {plan.commit[:7]}\n")
        print("確かめたこと（読み取りだけ）")
        _show_results(results)
        print()
        blocked = any(st != OK for st, _ in results)
        if not a.apply:
            show_plan(plan)
            print()
            print("NG または不明があるので、 このままでは --apply できません。"
                  if blocked else
                  "問題が無ければ、 --apply を付けて実行してください"
                  "（Organization の Owner）。")
            return 1 if blocked else 0
        if blocked:
            print("NG または不明があるので、 何も変えずに止めました。")
            return 1
        return apply(api, plan, repo, a.resume, sleep)
    except ApiError as e:
        print(f"NG: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

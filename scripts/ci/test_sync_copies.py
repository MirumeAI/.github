#!/usr/bin/env python3
"""`sync_copies.py`（写しの同期の Pull Request をまとめて作る道具）のテスト。

守ること:
  ・`plan` は何も作らない（Issue・Branch・push・Pull Request が無い）
  ・`apply` は、 写しを正本とバイトで同じにし、 `CLAUDE.md` と運用の現状の件数の
    行を直し、 作業 Branch にだけ push し、 Draft の Pull Request を作る。 main は
    変えない。 差が無い Repository には何も作らない
  ・`.ci-copy-allow` に書かれた写しは触らない。 正本の履歴に無い中身の写しは、
    `--overwrite-unknown` が無ければ置き換えずに止まる
  ・流し直しても、 Issue と Pull Request を二重に作らない。 Required Verification が
    落ちた Repository は Pull Request を作らずに止まり、 直して流し直すと続きから進む。
    main が進んだら取り込み、 検査を流し直して本文の記録の行を直す
  ・**顧客 Repository の名前を出力に出さない**（失敗したときの出力を含む）
  ・作業 Branch の形でない Branch には push しない。 正本の main に入っていない
    Pull Request からは同期しない。 Pull Request の雛形が変わったら止まる

GitHub は、 手元の bare repository と、 gh の代わりの小さな道具でまねる。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TOOL = HERE / "sync_copies.py"

_spec = importlib.util.spec_from_file_location("sync_copies", TOOL)
S = importlib.util.module_from_spec(_spec)
sys.modules["sync_copies"] = S                  # dataclass が自分の module を引くため
_spec.loader.exec_module(S)

#: 架空の顧客 Repository の名前（出力に出てはいけない）
SECRET = ("customer-alpha-part", "customer-beta-part")

FAKE_GH = r'''
import base64, json, os, sys
db_path = os.environ["FAKE_GH_DB"]
with open(db_path, encoding="utf-8") as f:
    db = json.load(f)
a = sys.argv[1:]


def opt(name):
    return a[a.index(name) + 1] if name in a else None


def save():
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump(db, f)


def body():
    with open(opt("--body-file"), encoding="utf-8") as f:
        return f.read()


db["calls"].append(a[:2])
if a[:2] == ["repo", "list"]:
    print(json.dumps(db["repos"]))
elif a[:2] == ["pr", "view"] and any(p["number"] == int(a[2]) for p in db["prs"].get(opt("-R") or "", [])):
    want = opt("--json").split(",")
    for p in db["prs"].get(opt("-R"), []):
        if p["number"] == int(a[2]):
            print(json.dumps({k: p.get(k) for k in want}))
elif a[:2] == ["pr", "view"]:
    print(json.dumps(db["source_pr"].get(a[2], {})))
elif a[0] == "api":
    with open(db["form"], "rb") as f:
        print(base64.b64encode(f.read()).decode())
elif a[:2] == ["issue", "list"]:
    print(json.dumps([{"number": n, "title": t} for n, t in db["issues"].get(opt("-R"), [])]))
elif a[:2] == ["issue", "create"]:
    repo = opt("-R")
    if repo in db["fail_issue"]:
        sys.stderr.write(f"HTTP 502: could not create issue in {repo}\n")
        save()
        sys.exit(1)
    n = db["next"]
    db["next"] += 1
    db["issues"].setdefault(repo, []).append([n, opt("--title")])
    db["issue_bodies"][f"{repo}#{n}"] = body()
    print(f"https://github.com/{repo}/issues/{n}")
elif a[:2] == ["pr", "list"]:
    repo = opt("-R")
    print(json.dumps([{"number": p["number"], "state": p["state"]}
                      for p in db["prs"].get(repo, []) if p["head"] == opt("--head")]))
elif a[:2] == ["pr", "create"]:
    repo = opt("-R")
    n = db["next"]
    db["next"] += 1
    db["prs"].setdefault(repo, []).append({
        "number": n, "state": "OPEN", "head": opt("--head"), "base": opt("--base"),
        "draft": "--draft" in a, "title": opt("--title"), "body": body()})
    print(f"https://github.com/{repo}/pull/{n}")
elif a[:2] == ["pr", "edit"]:
    for p in db["prs"][opt("-R")]:
        if p["number"] == int(a[2]):
            p["body"] = body()
else:
    sys.stderr.write("fake gh: unknown " + " ".join(a) + "\n")
    save()
    sys.exit(2)
save()
'''

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_NOSYSTEM": "1"}


def git(*args, cwd=None) -> str:
    r = subprocess.run(["git", *map(str, args)], cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **GIT_ENV))
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(map(str, args))}: {r.stderr}")
    return r.stdout.strip()


#: 写しのうち、 道具が中身を使うもの（Issue の本文を確かめる）。 本物の中身に版の印を足す
REAL = (".claude/scripts/issue-form.sh",)


def v(rel: str, ver: int) -> bytes:
    base = (ROOT / rel).read_bytes() if rel in REAL else b""
    return base + f"# {rel} の第 {ver} 版\n".encode()


class World:
    """正本・写しを持つ Repository・gh の代わりを、 一時の dir に作る。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.remotes = base / "remotes"
        self.remotes.mkdir()
        self.state = base / "state"
        self.bin = base / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        self.rv_fail: set = set()
        self.rv_fail_rc_only: set = set()       # 記録の行らしい文は出たが、 終了コードが失敗
        self.rv_line: dict = {}                 # 名前 → 記録の行の差し替え（基準を含まない・道具が正本と違う）
        self.rv_calls: list = []
        self.on_rv = None                       # 検査の間に起きること（push 先が変わるなど）
        self._canonical()
        self.db_path = base / "gh.json"
        self.db = {"repos": [], "source_pr": {"97": {"state": "MERGED", "mergeCommit": {"oid": self.canon_head}}},
                   "form": str(ROOT / ".github" / "ISSUE_TEMPLATE" / "feature.yml"),
                   "issues": {}, "issue_bodies": {}, "prs": {}, "fail_issue": [], "next": 100, "calls": []}
        self.add_repo(".github", {}, archived=False, make=False)
        self.save()

    # ---- 正本
    def _canonical(self) -> None:
        work = self.base / "canon-src"
        work.mkdir()
        git("init", "-q", "-b", "main", work)
        for rel in (".github/PULL_REQUEST_TEMPLATE.md",):
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(ROOT / rel, work / rel)
        for ver in (1, 2):
            for rel in S.SHARED:
                (work / rel).parent.mkdir(parents=True, exist_ok=True)
                (work / rel).write_bytes(v(rel, ver))
            git("-C", work, "add", "-A")
            git("-C", work, "commit", "-q", "-m", f"第 {ver} 版")
        bare = self.remotes / ".github.git"
        git("clone", "-q", "--bare", work, bare)
        self.canon = self.base / "canon"
        git("clone", "-q", bare, self.canon)
        self.canon_head = git("-C", self.canon, "rev-parse", "HEAD")

    def canon_commit(self, rel: str, data: bytes) -> None:
        (self.canon / rel).write_bytes(data)
        git("-C", self.canon, "add", "-A")
        git("-C", self.canon, "commit", "-q", "-m", "正本を変える")
        git("-C", self.canon, "push", "-q", "origin", "HEAD:main")

    # ---- 写しを持つ Repository
    def add_repo(self, name: str, files: dict, archived=False, make=True) -> None:
        self.db["repos"].append({"name": name, "isArchived": archived})
        if not make:
            self.save()
            return
        work = self.base / f"src-{name}"
        work.mkdir()
        git("init", "-q", "-b", "main", work)
        for rel, data in files.items():
            (work / rel).parent.mkdir(parents=True, exist_ok=True)
            (work / rel).write_bytes(data)
        git("-C", work, "add", "-A")
        git("-C", work, "commit", "-q", "-m", "初め")
        git("clone", "-q", "--bare", work, self.remotes / f"{name}.git")
        self.save()

    def remote_show(self, name: str, ref: str, rel: str) -> bytes:
        r = subprocess.run(["git", "--git-dir", str(self.remotes / f"{name}.git"), "show", f"{ref}:{rel}"],
                           capture_output=True)
        return r.stdout if r.returncode == 0 else b""

    def remote_heads(self, name: str) -> dict:
        out = git("--git-dir", self.remotes / f"{name}.git", "for-each-ref", "--format=%(refname) %(objectname)",
                  "refs/heads")
        return dict(ln.split() for ln in out.splitlines())

    def push_main(self, name: str, rel: str, data: bytes) -> None:
        work = self.base / f"push-{name}"
        if not work.exists():
            git("clone", "-q", self.remotes / f"{name}.git", work)
        git("-C", work, "pull", "-q")
        (work / rel).write_bytes(data)
        git("-C", work, "add", "-A")
        git("-C", work, "commit", "-q", "-m", "main を進める")
        git("-C", work, "push", "-q", "origin", "HEAD:main")

    # ---- gh の代わりの記録
    def save(self) -> None:
        self.db_path.write_text(json.dumps(self.db), encoding="utf-8")

    def load(self) -> dict:
        self.db = json.loads(self.db_path.read_text(encoding="utf-8"))
        return self.db

    # ---- 検査の代わり
    def rv(self, wt: Path, head: str, work: Path):
        assert work.is_dir() and not any(work.iterdir()), "検査の道具には空の作業の置き場を渡す"
        assert self.state in work.parents, "検査の道具の作業の置き場は、 状態の置き場の中"
        assert len(str(work)) <= len(str(self.state)) + 12, "検査の道具の作業の置き場の道は短く（ソケットの道の上限）"
        url = git("-C", wt, "remote", "get-url", "origin")
        name = Path(url).name[:-len(".git")]
        self.rv_calls.append((name, head))
        if self.on_rv:
            self.on_rv(wt)
        if name in self.rv_fail:
            return 1, f"結果: FAIL（{S.ORG}/{name} head {head[:12]}）\n"
        contains, tools = self.rv_line.get(name, ("はい", "正本と同じ"))
        text = ("結果: PASS\n記録の行（PR に書く）:\n"
                f"{S.RECORD}（{S.ORG}/{name} head {head[:12]}・基準 origin/main 0123456789ab を含む: {contains}・"
                f"正本の道具 MirumeAI/.github origin/main 0123456789ab・道具 0123456789ab（{tools}）・"
                "試験 10 件・飛ばした試験 0 件・1 秒）\n")
        return (1 if name in self.rv_fail_rc_only else 0), text

    def wt(self, alias: str) -> Path:
        return self.state / "runs" / "github-97" / "wt" / alias

    def state_file(self) -> Path:
        return self.state / "runs" / "github-97" / "state.json"

    def main(self, *argv) -> "tuple[int, str]":
        self.save()
        out = io.StringIO()
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_GH_DB=str(self.db_path),
                   **GIT_ENV)
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = S.main([*argv, "--state-dir", str(self.state), "--canonical", str(self.canon),
                           "--guard-cases", "257"],
                          remote_base=str(self.remotes), rv=self.rv, sleep=lambda s: None)
        self.load()
        return code, out.getvalue()


def claude_md(count: int) -> bytes:
    return f"# CLAUDE.md\n\n挙動は `scripts/ci/test_guard_main.sh` が {count} ケースで検証している。\n".encode()


OPS_ROW = "| `guard-main.sh` の回帰テスト | {} ケース。正本の検査として手元で流す |\n"


class SyncTest(unittest.TestCase):

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.w = World(Path(td.name))
        old = {rel: v(rel, 1) for rel in S.SHARED}
        new = {rel: v(rel, 2) for rel in S.SHARED}
        # development-docs: 前の正本の写しと、 件数の行が 2 か所（運用の現状は BOM つき）
        self.w.add_repo("development-docs", {
            **old, "CLAUDE.md": claude_md(178), "README.md": b"docs\n",
            "00_management/current_github_operation.md": b"\xef\xbb\xbf# \xe9\x81\x8b\xe7\x94\xa8\n\n"
            + OPS_ROW.format(178).encode()})
        # PDM: 写しの一部だけを持つ
        self.w.add_repo("PDM", {S.GUARD_TEST: v(S.GUARD_TEST, 1), ".claude/hooks/guard-main.sh":
                                v(".claude/hooks/guard-main.sh", 1), "CLAUDE.md": claude_md(178)})
        # 顧客 Repository 1: もう正本と同じ（差が無い）
        self.w.add_repo(SECRET[0], {**new, "CLAUDE.md": claude_md(257)})
        # 顧客 Repository 2: 1 つを意図して違えている（.ci-copy-allow）
        allow_rel = "scripts/ci/import_check.py"
        self.allow_rel = allow_rel
        self.w.add_repo(SECRET[1], {**old, allow_rel: b"# hand-tuned\n", "CLAUDE.md": claude_md(178),
                                     S.ALLOW_FILE: f"{allow_rel}  # この案件だけの理由\n".encode()})
        self.w.add_repo("archived-thing", {}, archived=True, make=False)

    def assertNoSecret(self, text: str) -> None:
        for name in SECRET:
            self.assertNotIn(name, text)
            self.assertNotIn("alpha", text)
            self.assertNotIn("beta", text)

    # ------------------------------------------------------------ plan
    def test_plan_changes_nothing_and_hides_names(self) -> None:
        code, out = self.w.main("plan")
        self.assertEqual(code, 0, out)
        self.assertNoSecret(out)
        self.assertIn("development-docs", out)
        self.assertIn("顧客 Repository 1", out)
        self.assertIn("顧客 Repository 2", out)
        self.assertNotIn("archived", out)
        self.assertRegex(out, r"dd .*同期が要る.*CLAUDE\.md の件数 178 → 257")
        self.assertRegex(out, r"c1 .*同期は要らない")
        self.assertRegex(out, r"c2 .*対象外 1")
        db = self.w.db
        self.assertEqual(db["issues"], {})
        self.assertEqual(db["prs"], {})
        self.assertEqual(set(self.w.remote_heads("PDM")), {"refs/heads/main"})

    # ------------------------------------------------------------ apply
    def test_apply_opens_draft_prs_on_work_branches_only(self) -> None:
        mains = {n: self.w.remote_heads(n)["refs/heads/main"] for n in ("development-docs", "PDM", *SECRET)}
        code, out = self.w.main("apply", "--source", "97", "--trailer", "Co-Authored-By: x <x@example.invalid>",
                                "--pr-footer", "🤖 generated")
        self.assertEqual(code, 0, out)
        self.assertNoSecret(out)
        db = self.w.db
        # main はどこも変わらない。 差が無い顧客 Repository 1 には何も作らない
        for n, sha in mains.items():
            self.assertEqual(self.w.remote_heads(n)["refs/heads/main"], sha, n)
        self.assertNotIn(f"{S.ORG}/{SECRET[0]}", db["issues"])
        self.assertEqual(set(self.w.remote_heads(SECRET[0])), {"refs/heads/main"})
        for name in ("development-docs", "PDM", SECRET[1]):
            repo = f"{S.ORG}/{name}"
            (issue, title), = db["issues"][repo]
            self.assertEqual(title, f"[Feature] {S.DEFAULT_SUBJECT}（MirumeAI/.github#97）")
            br = f"feature/{issue}-sync-copies"
            heads = self.w.remote_heads(name)
            self.assertEqual(set(heads), {"refs/heads/main", f"refs/heads/{br}"})
            (pr,) = db["prs"][repo]
            self.assertTrue(pr["draft"])
            self.assertEqual((pr["head"], pr["base"]), (br, "main"))
            self.assertIn(f"Closes #{issue}", pr["body"])
            self.assertIn(f"{S.RECORD}（{S.ORG}/{name} head {heads['refs/heads/' + br][:12]}", pr["body"])
            self.assertTrue(pr["body"].rstrip().endswith("🤖 generated"))
            self.assertIn("差が写しと決まった行〔回帰テストの件数の行。数だけ〕だけ", pr["body"])
            # 本文の見出しは雛形どおり
            self.assertEqual(tuple(ln[3:] for ln in pr["body"].splitlines() if ln.startswith("## ")), S.PR_HEADINGS)
            msg = git("--git-dir", self.w.remotes / f"{name}.git", "log", "-1", "--format=%B", br)
            self.assertIn("Co-Authored-By: x <x@example.invalid>", msg)
        # 写しは正本とバイトで同じ。 件数の行は 257
        dd = "development-docs"
        br = self.w.db["prs"][f"{S.ORG}/{dd}"][0]["head"]
        for rel in S.SHARED:
            self.assertEqual(self.w.remote_show(dd, br, rel), v(rel, 2), rel)
        self.assertIn(b"257 \xe3\x82\xb1\xe3\x83\xbc\xe3\x82\xb9", self.w.remote_show(dd, br, "CLAUDE.md"))
        ops = self.w.remote_show(dd, br, "00_management/current_github_operation.md")
        self.assertTrue(ops.startswith(b"\xef\xbb\xbf"))
        self.assertIn(OPS_ROW.format(257).encode(), ops)
        changed = git("--git-dir", self.w.remotes / f"{dd}.git", "diff", "--name-only", f"main...{br}").split()
        self.assertEqual(set(changed), set(S.SHARED) | {"CLAUDE.md", "00_management/current_github_operation.md"})
        # PDM は持っている写しだけ
        br = self.w.db["prs"][f"{S.ORG}/PDM"][0]["head"]
        changed = git("--git-dir", self.w.remotes / "PDM.git", "diff", "--name-only", f"main...{br}").split()
        self.assertEqual(set(changed), {S.GUARD_TEST, ".claude/hooks/guard-main.sh", "CLAUDE.md"})
        # 意図して違えた写しは触らない
        repo2 = SECRET[1]
        br = self.w.db["prs"][f"{S.ORG}/{repo2}"][0]["head"]
        self.assertEqual(self.w.remote_show(repo2, br, self.allow_rel), b"# hand-tuned\n")
        # Issue の本文は正本の Issue Form の道具が OK を出した形
        for key, text in db["issue_bodies"].items():
            self.assertIn("### 担当領域\n\nInfrastructure", text, key)

    def test_rerun_does_not_duplicate(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97")[0], 0)
        before = json.dumps([self.w.db["issues"], [(r, [p["number"] for p in ps]) for r, ps in self.w.db["prs"].items()]])
        rv_before = len(self.w.rv_calls)
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 0, out)
        after = json.dumps([self.w.db["issues"], [(r, [p["number"] for p in ps]) for r, ps in self.w.db["prs"].items()]])
        self.assertEqual(before, after)
        self.assertEqual(len(self.w.rv_calls), rv_before)       # head が同じなら流し直さない

    def test_lost_state_does_not_duplicate(self) -> None:
        # Issue や Pull Request を作った直後に止まり、 状態の記録が残らなかったときも二重に作らない
        self.assertEqual(self.w.main("apply", "--source", "97")[0], 0)
        (self.w.state / "runs" / "github-97" / "state.json").unlink()
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 0, out)
        for name in ("development-docs", "PDM", SECRET[1]):
            self.assertEqual(len(self.w.db["issues"][f"{S.ORG}/{name}"]), 1, name)
            self.assertEqual(len(self.w.db["prs"][f"{S.ORG}/{name}"]), 1, name)

    def test_failed_verification_stops_that_repo_and_resumes(self) -> None:
        self.w.rv_fail = {SECRET[1]}
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 1)
        self.assertNoSecret(out)
        self.assertRegex(out, r"c2 .*顧客 Repository 2: 止まりました — Required Verification が PASS しませんでした")
        self.assertNotIn(f"{S.ORG}/{SECRET[1]}", self.w.db["prs"])
        self.assertEqual(len(self.w.db["prs"][f"{S.ORG}/PDM"]), 1)
        # 直して流し直すと、 同じ Issue のまま続きから
        self.w.rv_fail = set()
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.w.db["issues"][f"{S.ORG}/{SECRET[1]}"]), 1)
        self.assertEqual(len(self.w.db["prs"][f"{S.ORG}/{SECRET[1]}"]), 1)

    def test_failed_exit_code_stops_even_with_record_line(self) -> None:
        self.w.rv_fail_rc_only = {"PDM"}
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("Required Verification が PASS しませんでした", out)
        self.assertNotIn(f"{S.ORG}/PDM", self.w.db["prs"])

    def test_main_moved_brings_it_in_and_updates_record(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97")[0], 0)
        repo = f"{S.ORG}/PDM"
        old_body = self.w.db["prs"][repo][0]["body"]
        self.w.push_main("PDM", "README.md", b"new\n")
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 0, out)
        br = self.w.db["prs"][repo][0]["head"]
        head = self.w.remote_heads("PDM")[f"refs/heads/{br}"]
        main = self.w.remote_heads("PDM")["refs/heads/main"]
        self.assertEqual(git("--git-dir", self.w.remotes / "PDM.git", "merge-base", main, head), main)
        body = self.w.db["prs"][repo][0]["body"]
        self.assertNotEqual(body, old_body)
        self.assertIn(f"head {head[:12]}", body)
        self.assertEqual(len(self.w.db["prs"][repo]), 1)

    def test_unknown_content_is_not_overwritten_without_flag(self) -> None:
        rel = ".claude/hooks/guard-main.sh"
        self.w.push_main("PDM", rel, b"# hand edit\n")
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("正本の履歴に無い中身", out)
        self.assertNotIn(f"{S.ORG}/PDM", self.w.db["issues"])
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm", "--overwrite-unknown")
        self.assertEqual(code, 0, out)
        (pr,) = self.w.db["prs"][f"{S.ORG}/PDM"]
        self.assertIn("正本の履歴に無い中身を置き換えた", pr["body"])
        self.assertEqual(self.w.remote_show("PDM", pr["head"], rel), v(rel, 2))

    def test_errors_do_not_show_customer_names(self) -> None:
        self.w.db["fail_issue"] = [f"{S.ORG}/{SECRET[1]}"]
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 1)
        self.assertIn("Issue を作れませんでした", out)
        self.assertNoSecret(out)

    def test_source_must_be_merged_into_canonical_main(self) -> None:
        self.w.db["source_pr"]["97"] = {"state": "OPEN", "mergeCommit": None}
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 2)
        self.assertIn("正本の main", out)
        self.assertEqual(self.w.db["issues"], {})

    def test_template_drift_stops_before_anything(self) -> None:
        rel = ".github/PULL_REQUEST_TEMPLATE.md"
        self.w.canon_commit(rel, (self.w.canon / rel).read_bytes() + "\n## 新しい見出し\n".encode())
        self.w.db["source_pr"]["97"]["mergeCommit"]["oid"] = git("-C", self.w.canon, "rev-parse", "HEAD")
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 2)
        self.assertIn("雛形", out)
        self.assertEqual(self.w.db["issues"], {})

    # ------------------------------------------------------------ 止まる場面（守りを 1 つずつ）
    def test_other_diff_stops_and_hides_paths(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "c2")[0], 0)
        wt = self.w.wt("c2")
        (wt / "notes-beta-plant.txt").write_text("x\n", encoding="utf-8")
        git("-C", wt, "add", "-A")
        git("-C", wt, "commit", "-q", "-m", "写しでない直し")
        code, out = self.w.main("apply", "--source", "97", "--only", "c2")
        self.assertEqual(code, 1)
        self.assertIn("ほかの差が 1 ファイル", out)
        self.assertNoSecret(out)                                  # 道（beta を含む）を出さない

    def test_remote_branch_with_unknown_commit_stops(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        br = self.w.db["prs"][f"{S.ORG}/PDM"][0]["head"]
        other = self.w.base / "other-pdm"
        git("clone", "-q", "-b", br, self.w.remotes / "PDM.git", other)
        (other / "x.txt").write_text("x\n", encoding="utf-8")
        git("-C", other, "add", "-A")
        git("-C", other, "commit", "-q", "-m", "ほかの人の commit")
        git("-C", other, "push", "-q", "origin", f"HEAD:{br}")
        self.w.push_main("PDM", "README.md", b"new\n")          # 手元の head が変わり、 push が要る
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("手元に無い commit", out)

    def test_record_must_contain_main_and_canonical_tools(self) -> None:
        for variant, msg in ((("いいえ", "正本と同じ"), "main を含みません"),
                             (("はい", "正本と違う"), "正本の main と違います")):
            self.w.rv_line = {"PDM": variant}
            code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
            self.assertEqual(code, 1, variant)
            self.assertIn(msg, out)
            self.assertNotIn(f"{S.ORG}/PDM", self.w.db["prs"])

    def test_clone_origin_must_match(self) -> None:
        self.assertEqual(self.w.main("plan")[0], 0)
        git("-C", self.w.state / "clones" / "pdm", "remote", "set-url", "--push", "origin",
            self.w.remotes / "development-docs.git")
        code, out = self.w.main("plan", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("clone の origin が違います", out)

    def test_canonical_origin_must_match(self) -> None:
        git("-C", self.w.canon, "remote", "set-url", "origin", self.w.remotes / "PDM.git")
        code, out = self.w.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("正本の clone", out)

    def test_closed_pr_stops_when_state_is_lost(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        self.w.db["prs"][f"{S.ORG}/PDM"][0]["state"] = "CLOSED"
        self.w.state_file().unlink()
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("既に閉じています", out)

    def test_non_work_branch_in_state_is_refused(self) -> None:
        self.w.rv_fail = {"PDM"}
        self.w.main("apply", "--source", "97", "--only", "pdm")       # 作業ツリーと commit まで（push しない）
        st = json.loads(self.w.state_file().read_text(encoding="utf-8"))
        st["targets"]["pdm"]["branch"] = "main"
        self.w.state_file().write_text(json.dumps(st), encoding="utf-8")
        self.w.rv_fail = set()
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("作業 Branch の形", out)
        self.assertEqual(set(self.w.remote_heads("PDM")), {"refs/heads/main"})

    def test_bad_issue_body_stops_before_creating(self) -> None:
        with mock.patch.object(S, "issue_body", lambda *a: "### 目的\n\nx\n"):
            code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 2)
        self.assertIn("Issue Form に合いません", out)
        self.assertEqual(self.w.db["issues"], {})

    def test_uncommitted_change_in_worktree_stops(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        (self.w.wt("pdm") / "scratch.txt").write_text("x\n", encoding="utf-8")
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("commit していない変更", out)

    def test_symlink_copy_is_refused(self) -> None:
        other = self.w.base / "sym-pdm"
        git("clone", "-q", self.w.remotes / "PDM.git", other)
        rel = ".claude/hooks/guard-main.sh"
        (other / rel).unlink()
        os.symlink("../../outside.txt", other / rel)
        git("-C", other, "add", "-A")
        git("-C", other, "commit", "-q", "-m", "symlink")
        git("-C", other, "push", "-q", "origin", "HEAD:main")
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm", "--overwrite-unknown")
        self.assertEqual(code, 1)
        self.assertIn("普通のファイルではありません", out)
        self.assertNotIn(f"{S.ORG}/PDM", self.w.db["issues"])

    # ------------------------------------------------------------ 続け方
    def test_lost_state_dir_resumes_from_pushed_branch(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97")[0], 0)
        shutil.rmtree(self.w.state)
        code, out = self.w.main("apply", "--source", "97")
        self.assertEqual(code, 0, out)
        for name in ("development-docs", "PDM", SECRET[1]):
            self.assertEqual(len(self.w.db["issues"][f"{S.ORG}/{name}"]), 1, name)
            self.assertEqual(len(self.w.db["prs"][f"{S.ORG}/{name}"]), 1, name)

    def test_canonical_advanced_is_written_in_body(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        rel = ".claude/hooks/guard-main.sh"
        self.w.canon_commit(rel, v(rel, 3))
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 0, out)
        self.assertIn("正本が進みました", out)
        (pr,) = self.w.db["prs"][f"{S.ORG}/PDM"]
        self.assertEqual(self.w.remote_show("PDM", pr["head"], rel), v(rel, 3))
        self.assertIn("同期を始めた後に正本の main が進んだ", pr["body"])

    def test_footer_and_trailer_are_kept_on_rerun(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm", "--pr-footer", "🤖 印",
                                     "--trailer", "Co-Authored-By: x <x@example.invalid>")[0], 0)
        rel = ".claude/hooks/guard-main.sh"
        self.w.canon_commit(rel, v(rel, 3))                     # 新しい commit が要る形にする
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")    # 終わりの行を付け忘れる
        self.assertEqual(code, 0, out)
        (pr,) = self.w.db["prs"][f"{S.ORG}/PDM"]
        self.assertTrue(pr["body"].rstrip().endswith("🤖 印"))
        msg = git("--git-dir", self.w.remotes / "PDM.git", "log", "-1", "--format=%B", pr["head"])
        self.assertIn("Co-Authored-By: x <x@example.invalid>", msg)

    def test_logs_hide_names_and_state_dir_is_private(self) -> None:
        self.w.rv_fail = {SECRET[1]}
        # 顧客 Repository にある Issue の題には案件の言葉が入りうる（Repository の名前の形でないので伏せられない）
        self.w.db["issues"][f"{S.ORG}/{SECRET[1]}"] = [[5, "beta-plant の調整"]]
        self.w.main("apply", "--source", "97")
        logs = list((self.w.state / "runs").rglob("*.log"))
        self.assertTrue(logs)
        for f in logs:
            self.assertNoSecret(f.read_text(encoding="utf-8"))
        self.assertEqual(self.w.state.stat().st_mode & 0o777, 0o700)

    # ------------------------------------------------------------ #102 で足した守り
    def test_symlink_in_worktree_is_refused(self) -> None:
        # 作業ツリーの写しが symlink なら、 リンクの先（作業ツリーの外）へ書かずに止まる
        self.w.rv_fail = {"PDM"}
        self.w.main("apply", "--source", "97", "--only", "pdm")          # 作業ツリーと commit まで
        wt, rel = self.w.wt("pdm"), ".claude/hooks/guard-main.sh"
        outside = self.w.base / "outside.txt"
        outside.write_bytes(b"outside\n")
        (wt / rel).unlink()
        os.symlink(outside, wt / rel)
        git("-C", wt, "add", "-A")
        git("-C", wt, "commit", "-q", "-m", "symlink")
        self.w.canon_commit(rel, v(rel, 3))                              # 写しを書き直す形にする
        self.w.rv_fail = set()
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("symlink です", out)
        self.assertEqual(outside.read_bytes(), b"outside\n")

    def test_push_target_is_checked_again_just_before_push(self) -> None:
        # 検査の間に push 先が変わっても、 その先へ push しない
        dd_before = self.w.remote_heads("development-docs")

        def swap(wt: Path) -> None:
            git("-C", wt, "remote", "set-url", "--push", "origin", self.w.remotes / "development-docs.git")
        self.w.on_rv = swap
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("clone の origin が違います", out)
        self.assertEqual(self.w.remote_heads("development-docs"), dd_before)
        self.assertEqual(set(self.w.remote_heads("PDM")), {"refs/heads/main"})

    def test_known_is_limited_to_canonical_main(self) -> None:
        # 正本の main の履歴に無い中身（別の Branch にだけある）は、 前の正本として置き換えない
        rel = ".claude/hooks/guard-main.sh"
        git("-C", self.w.canon, "switch", "-q", "-c", "side")
        (self.w.canon / rel).write_bytes(v(rel, 9))
        git("-C", self.w.canon, "commit", "-q", "-am", "main に入っていない案")
        git("-C", self.w.canon, "switch", "-q", "main")
        self.w.push_main("PDM", rel, v(rel, 9))
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("正本の履歴に無い中身", out)
        self.assertIn("人の判断が要る", out)

    def test_rv_log_is_written_masked_after_the_check(self) -> None:
        # 検査の出力は、 終わってから名前を伏せて書く（顧客 Repository の記録の行には名前が入る）
        code, out = self.w.main("apply", "--source", "97", "--only", "c2")
        self.assertEqual(code, 0, out)
        (log,) = (self.w.state / "runs" / "github-97" / "logs").glob("c2-rv-*.log")
        text = log.read_text(encoding="utf-8")
        self.assertIn(S.RECORD, text)
        self.assertIn("顧客 Repository 2", text)
        self.assertNoSecret(text)

    def test_default_rv_does_not_write_the_raw_output(self) -> None:
        # 正本の検査の道具の出力は、 ファイルに書かずに受け取る（途中で止めても、 伏せる前の記録が残らない）
        root = self.w.base / "fake-canon"
        (root / "scripts" / "ci").mkdir(parents=True)
        (root / "scripts" / "ci" / "run_required_verification.py").write_text(
            f"import sys\nprint('{S.ORG}/{SECRET[0]}', sys.argv[1:])\n", encoding="utf-8")
        before = sorted(self.w.base.rglob("*"))
        work = self.w.base / "rv-work-x"
        work.mkdir()
        before = sorted(self.w.base.rglob("*"))
        rc, text = S.default_rv(root)(self.w.base, "0" * 40, work)
        self.assertEqual(rc, 0)
        self.assertIn(SECRET[0], text)
        self.assertIn(f"'--work', '{work}'", text, "検査の道具に作業の置き場を渡す")
        self.assertEqual(sorted(self.w.base.rglob("*")), before)

    def test_existing_state_dir_keeps_its_mode(self) -> None:
        self.w.state.mkdir(mode=0o755)
        os.chmod(self.w.state, 0o755)
        code, out = self.w.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("本人だけが読める dir", out)
        self.assertEqual(self.w.state.stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.w.state.iterdir()), [])
        os.chmod(self.w.state, 0o700)
        self.assertEqual(self.w.main("plan")[0], 0)
        self.assertEqual(self.w.state.stat().st_mode & 0o777, 0o700)

    def test_scope_names_the_kinds_of_copies_changed(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97")[0], 0)
        bodies = {n: self.w.db["prs"][f"{S.ORG}/{n}"][0]["body"] for n in ("development-docs", "PDM")}
        self.assertIn("## 影響範囲\n\nこの Repository の写し（ガードの回帰テスト・ガード・回帰テストの件数の行）。",
                      bodies["PDM"])
        self.assertIn("## 影響範囲\n\nこの Repository の写し（検査の道具・ガードの回帰テスト・ガード・"
                      "Issue Form の道具・共通の command・回帰テストの件数の行）。", bodies["development-docs"])
        a = S.Analysis("x", files=[(".claude/commands/pr.md", "old")])
        self.assertEqual(S.scope(a), "この Repository の写し（共通の command）。")

    def test_cleanup_removes_only_merged_and_unchanged(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm,dd")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        def pdm_pr() -> dict:                      # main() の後は gh の記録を読み直すので、 その都度引く
            return self.w.db["prs"][f"{S.ORG}/PDM"][0]
        pdm_br = pdm_pr()["head"]
        # まだ merge されていない Pull Request には触らない
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 0, out)
        self.assertIn("まだ merge されていない", out)
        self.assertTrue(self.w.wt("pdm").exists())
        # merge された head と作業ツリーの先頭が違えば、 片付けない
        pdm_pr().update(state="MERGED", headRefOid="0" * 40)
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("merge された head と違います", out)
        self.assertTrue(self.w.wt("pdm").exists())
        # commit していない変更があれば、 片付けない
        pdm_pr()["headRefOid"] = self.w.remote_heads("PDM")[f"refs/heads/{pdm_br}"]
        (self.w.wt("pdm") / "scratch.txt").write_text("x\n", encoding="utf-8")
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("commit していない変更", out)
        (self.w.wt("pdm") / "scratch.txt").unlink()
        # merge されて、 先頭が同じなら片付ける。 リモートの Branch には触らない
        code, out = self.w.main("cleanup", "--source", "97")
        self.assertEqual(code, 0, out)
        self.assertIn("片付けた", out)
        self.assertNoSecret(out)
        self.assertFalse(self.w.wt("pdm").exists())
        self.assertEqual(git("-C", clone, "branch", "--list", pdm_br), "")
        self.assertIn(f"refs/heads/{pdm_br}", self.w.remote_heads("PDM"))
        self.assertTrue(self.w.wt("dd").exists(), "merge されていない development-docs は残す")

    def test_cleanup_sees_untracked_files_whatever_the_git_config(self) -> None:
        # status.showUntrackedFiles=no でも、 追跡していないファイルのある作業ツリーは片付けない
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{pr['head']}"])
        git("-C", clone, "config", "status.showUntrackedFiles", "no")
        (self.w.wt("pdm") / "notes.txt").write_text("x\n", encoding="utf-8")
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("commit していない変更", out)
        self.assertTrue((self.w.wt("pdm") / "notes.txt").exists())

    def test_cleanup_checks_the_origin_and_the_branch_shape(self) -> None:
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{pr['head']}"])
        # clone の push 先が違えば、 片付けない
        git("-C", clone, "remote", "set-url", "--push", "origin", self.w.remotes / "development-docs.git")
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("clone の origin が違います", out)
        self.assertTrue(self.w.wt("pdm").exists())
        git("-C", clone, "remote", "set-url", "--push", "origin", self.w.remotes / "PDM.git")
        # 状態の Branch が作業 Branch の形でなければ、 片付けない
        st = json.loads(self.w.state_file().read_text(encoding="utf-8"))
        st["targets"]["pdm"]["branch"] = "main"
        self.w.state_file().write_text(json.dumps(st), encoding="utf-8")
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("作業 Branch の形", out)
        self.assertTrue(self.w.wt("pdm").exists())

    def test_apply_does_not_touch_a_closed_or_cleaned_sync(self) -> None:
        # merge 済み（閉じた）同期の Pull Request に、 流し直しで push・本文の書き換えをしない
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        br = pr["head"]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{br}"])
        body = pr["body"]
        rel = ".claude/hooks/guard-main.sh"
        self.w.canon_commit(rel, v(rel, 3))                     # 正本が進み、 新しい commit が要る形
        heads = self.w.remote_heads("PDM")
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("もう開いていません", out)
        self.assertEqual(self.w.remote_heads("PDM"), heads)
        self.assertEqual(self.w.db["prs"][f"{S.ORG}/PDM"][0]["body"], body)
        # 片付けた後も同じ
        self.assertEqual(self.w.main("cleanup", "--source", "97", "--only", "pdm")[0], 0)
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn("片付け済み", out)
        self.assertEqual(self.w.remote_heads("PDM"), heads)

    def test_cleanup_keeps_a_local_branch_that_moved(self) -> None:
        # 作業ツリーが無くても、 ローカルの作業 Branch の先頭が merge された head と違えば消さない
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        br = pr["head"]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{br}"])
        git("-C", clone, "worktree", "remove", self.w.wt("pdm"))
        git("-C", clone, "branch", "-f", br, "origin/main")
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1)
        self.assertIn(f"ローカルの {br} の先頭が", out)
        self.assertNotEqual(git("-C", clone, "branch", "--list", br), "")

    def test_apply_sees_untracked_files_in_the_worktree_whatever_the_git_config(self) -> None:
        # status.showUntrackedFiles=no でも、 追跡していないファイルのある作業ツリーでは続けない（#118）
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        git("-C", clone, "config", "status.showUntrackedFiles", "no")
        (self.w.wt("pdm") / "notes.txt").write_text("x\n", encoding="utf-8")
        rel = ".claude/hooks/guard-main.sh"
        self.w.canon_commit(rel, v(rel, 3))                     # 正本が進み、 新しい commit が要る形
        heads = self.w.remote_heads("PDM")
        code, out = self.w.main("apply", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 1, out)
        self.assertIn("commit していない変更", out)
        self.assertEqual(self.w.remote_heads("PDM"), heads)
        self.assertTrue((self.w.wt("pdm") / "notes.txt").exists())

    def test_cleanup_prunes_a_worktree_removed_by_hand(self) -> None:
        # 作業ツリーの dir を手で消しても（登録は残る）、 登録を外してから作業 Branch を消す（#118）
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        clone = self.w.state / "clones" / "pdm"
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        br = pr["head"]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{br}"])
        shutil.rmtree(self.w.wt("pdm"))
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 0, out)
        self.assertIn("片付けた", out)
        self.assertEqual(git("-C", clone, "branch", "--list", br), "")

    def test_cleanup_removes_only_its_own_rv_work(self) -> None:
        # 検査の作業の置き場（伏せていない段ごとの記録）は、 その同期のものだけを消す（#118）
        made = []

        def on_rv(wt):
            made.append(sorted((self.w.state / "w").iterdir())[-1])
        self.w.on_rv = on_rv
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm,dd")[0], 0)
        work_root = self.w.state / "w"
        other = work_root / "other"
        other.mkdir()
        st = json.loads(self.w.state_file().read_text(encoding="utf-8"))
        names = {a: st["targets"][a]["rv_work"] for a in ("pdm", "dd")}
        self.assertEqual(len(names["pdm"]), 1)
        for a in ("pdm", "dd"):
            (work_root / names[a][0] / "job-01").mkdir()
            (work_root / names[a][0] / "job-01" / "step.log").write_text("伏せていない記録\n", encoding="utf-8")
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{pr['head']}"])
        code, out = self.w.main("cleanup", "--source", "97")
        self.assertEqual(code, 0, out)
        self.assertFalse((work_root / names["pdm"][0]).exists())
        self.assertTrue((work_root / names["dd"][0]).exists(), "merge されていない同期の記録は残す")
        self.assertTrue(other.exists(), "道具が作っていない dir には触らない")
        st = json.loads(self.w.state_file().read_text(encoding="utf-8"))
        self.assertNotIn("rv_work", st["targets"]["pdm"])
        self.assertEqual(len(made), 2)

    def test_cleanup_does_not_follow_odd_rv_work_names(self) -> None:
        # 状態に書かれた名前が 1 段の名前でなければ（../ など）、 消さない（#118）
        self.assertEqual(self.w.main("apply", "--source", "97", "--only", "pdm")[0], 0)
        keep = self.w.base / "keep"
        keep.mkdir()
        st = json.loads(self.w.state_file().read_text(encoding="utf-8"))
        st["targets"]["pdm"]["rv_work"] += ["../../keep", str(keep), ".."]
        self.w.state_file().write_text(json.dumps(st), encoding="utf-8")
        pr = self.w.db["prs"][f"{S.ORG}/PDM"][0]
        pr.update(state="MERGED", headRefOid=self.w.remote_heads("PDM")[f"refs/heads/{pr['head']}"])
        code, out = self.w.main("cleanup", "--source", "97", "--only", "pdm")
        self.assertEqual(code, 0, out)
        self.assertTrue(keep.exists())
        self.assertTrue(self.w.state.exists())

    def test_a_long_state_dir_is_noted(self) -> None:
        self.assertIsNone(S.long_state_dir_note(Path("/home/user/.cache/mirumeai/sync-copies")))
        note = S.long_state_dir_note(Path("/home/user/" + "x" * 60 + "/sync-copies"))
        self.assertIn("Unix ソケットの道の上限", note)
        self.assertIn("--state-dir", note)

    def test_cleanup_without_state_stops(self) -> None:
        # 正本に入った --source でも、 状態が無ければ何を片付けるか分からないので止まる
        self.w.db["source_pr"]["98"] = dict(self.w.db["source_pr"]["97"])
        code, out = self.w.main("cleanup", "--source", "98")
        self.assertEqual(code, 2)
        self.assertIn("この --source の状態がありません", out)

    def test_unknown_alias_is_rejected(self) -> None:
        code, out = self.w.main("plan", "--only", "zz")
        self.assertEqual(code, 2)
        self.assertIn("知らない別名", out)


class GuardCasesTest(unittest.TestCase):
    """件数は、 正本の main のガードの回帰テストを流して数える（不合格があれば止まる）。"""

    def _count(self, script: str) -> int:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            src = base / "src"
            src.mkdir()
            git("init", "-q", "-b", "main", src)
            (src / ".claude/hooks").mkdir(parents=True)
            (src / ".claude/hooks/guard-main.sh").write_text("exit 0\n", encoding="utf-8")
            (src / "scripts/ci").mkdir(parents=True)
            (src / S.GUARD_TEST).write_text(script, encoding="utf-8")
            git("-C", src, "add", "-A")
            git("-C", src, "commit", "-q", "-m", "x")
            git("clone", "-q", "--bare", src, base / "r.git")
            git("clone", "-q", base / "r.git", base / "canon")
            run = S.Runner(base / "log", lambda s: None)
            return S.guard_cases(S.Canonical(base / "canon", run), run, None)

    def test_counts_passes_of_canonical_main(self) -> None:
        script = 'test -f "$CI_ROOT/.claude/hooks/guard-main.sh" || exit 2\necho "合格 7 / 不合格 0"\n'
        self.assertEqual(self._count(script), 7)

    def test_stops_when_a_case_fails(self) -> None:
        with self.assertRaises(S.Fatal):
            self._count('echo "合格 6 / 不合格 1"\nexit 1\n')


class WorkBranchTest(unittest.TestCase):

    def test_only_work_branches(self) -> None:
        S.check_work_branch("feature/12-sync-copies")
        for bad in ("main", "feature/x-sync", "feature/12-x:main", "feature/12-X", "fix/12-x",
                    "feature/12-x/../main", "+feature/12-x", "feature/12-x\n"):
            with self.assertRaises(S.Stop, msg=bad):
                S.check_work_branch(bad)


class CheckOriginTest(unittest.TestCase):
    """push の直前にも、 fetch と push の先がその Repository であることを確かめる。"""

    def test_push_url_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "r"
            git("init", "-q", repo)
            git("-C", repo, "remote", "add", "origin", "/x/PDM.git")
            run = S.Runner(Path(td) / "log", lambda s: None)
            S.check_origin(run, repo, "/x/PDM.git")
            git("-C", repo, "remote", "set-url", "--push", "origin", "/x/other.git")
            with self.assertRaises(S.Stop):
                S.check_origin(run, repo, "/x/PDM.git")


class MaskerTest(unittest.TestCase):

    def test_masks_names_and_urls(self) -> None:
        m = S.Masker()
        m.add("customer-alpha-part", "顧客 Repository 1")
        text = m("https://github.com/MirumeAI/customer-alpha-part/pull/3 と customer-alpha-part")
        self.assertEqual(text, "https://github.com/顧客 Repository 1/pull/3 と 顧客 Repository 1")


if __name__ == "__main__":
    unittest.main()

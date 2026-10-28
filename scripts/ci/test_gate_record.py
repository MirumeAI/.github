#!/usr/bin/env python3
"""`gate_record.py`（Reviewer への頼み・門の記録・merge の後の記録・本文の記録の行を作る道具）のテスト。

守ること:
  ・GitHub へ書き込まない（gh は `pr view` だけ）
  ・作業ツリー・Pull Request・検査の記録の行の head が 1 つでも違えば、 文を作らずに止まる。
    基準を含まない・道具が正本と違う・FAIL の検査の記録も使わない
  ・門の記録は、 main が検査の基準から進んだ・Reviewer の最後の結果が merge する head を
    見ていない・BLOCKER・MAJOR・IN-PR が 0 でない・In-PR の残りがある・人の判断の後に
    head が変わった、 のどれでも止まる。 base が main でない Pull Request は扱わない
  ・Reviewer の数の写し違い（BLOCKER と MAJOR を In-PR に数えない・FAIL なのに In-PR が 0）は受け付けない
  ・merge の命令は、 merge する head を固定し、 main が進んでいたら merge まで進まない形
  ・**顧客 Repository の名前を画面に出さない**（文は `--out` のファイルにだけ書く。 止まったときの文にも出さない）

GitHub は、 手元の bare repository と、 gh の代わりの小さな道具でまねる。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("gate_record", HERE / "gate_record.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

#: 架空の顧客 Repository の名前（出力に出てはいけない）
SECRET = "customer-alpha-part"

FAKE_GH = r'''
import json, os, sys
path = os.environ["FAKE_GH_DB"]
with open(path, encoding="utf-8") as f:
    db = json.load(f)
a = sys.argv[1:]
db["calls"].append(a)
with open(path, "w", encoding="utf-8") as f:
    json.dump(db, f)
if a[:2] == ["pr", "view"] and len(a) == 5 and a[3] == "--json":
    pr = db["prs"].get(a[2])
    if pr is None:
        sys.stderr.write("no pull requests found\n")
        sys.exit(1)
    print(json.dumps({k: pr.get(k) for k in a[4].split(",")}))
else:
    sys.stderr.write("fake gh: unknown\n")
    sys.exit(2)
'''

GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}


def git(*args, cwd=None) -> str:
    r = subprocess.run(["git", *map(str, args)], cwd=cwd, capture_output=True, text=True,
                       env=dict(os.environ, **GIT_ENV))
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(map(str, args))}: {r.stderr}")
    return r.stdout.strip()


def ok(result: str = "PASS") -> dict:
    return {"result": result, "blocker": 0, "major": 0, "in_pr": 0}


class World:
    """origin（bare）・作業ツリー・gh の代わり・検査の出力を、 一時の dir に作る。"""

    def __init__(self, base: Path, repo: str = "development-docs", wt_name: str = "wt") -> None:
        self.base = base
        self.slug = f"MirumeAI/{repo}"
        self.origin = base / "origin.git"
        self.wt = base / wt_name
        git("init", "-q", "--bare", self.origin)
        git("init", "-q", self.wt)
        git("remote", "add", "origin", self.origin, cwd=self.wt)
        self.commit("a.txt", "1\n")
        git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=self.wt)
        git("fetch", "-q", "origin", cwd=self.wt)
        self.base_sha = git("rev-parse", "origin/main", cwd=self.wt)
        git("switch", "-q", "-c", "feature/7-x", cwd=self.wt)
        self.first = self.commit("b.txt", "1\n")
        self.head = self.commit("b.txt", "2\n")
        git("push", "-q", "origin", "HEAD:refs/heads/feature/7-x", cwd=self.wt)
        self.bin = base / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        self.db_path = base / "gh.json"
        self.db = {"calls": [], "prs": {"7": {
            "url": f"https://github.com/{self.slug}/pull/7", "state": "OPEN", "headRefOid": self.head,
            "baseRefName": "main", "isDraft": True, "mergeCommit": None,
            "body": "## 確認結果\r\n\r\n- [x] 通った\r\n\r\n" + self.line(self.first) + "\r\n\r\n## 影響範囲\r\n"}}}
        self.n = 0

    def commit(self, name: str, text: str) -> str:
        (self.wt / name).write_text(text, encoding="utf-8")
        git("add", "-A", cwd=self.wt)
        git("commit", "-q", "-m", name, cwd=self.wt)
        return git("rev-parse", "HEAD", cwd=self.wt)

    def line(self, head: str, base: "str | None" = None, has: str = "はい", same: str = "正本と同じ",
             slug: "str | None" = None, skipped: int = 0) -> str:
        return (f"{G.RECORD_PASS}（{slug or self.slug} head {head[:12]}・基準 origin/main {(base or self.base_sha)[:12]}"
                f" を含む: {has}・正本の道具 MirumeAI/.github origin/main {'a' * 12}・道具 {'b' * 12}（{same}）"
                f"・workflow 1・job 2・段 13・試験 320 件・飛ばした試験 {skipped} 件・19 秒）")

    def file(self, text: str, suffix: str = ".txt") -> Path:
        self.n += 1
        p = self.base / f"f{self.n}{suffix}"
        p.write_text(text, encoding="utf-8")
        return p

    def rv(self, steps: "str | None" = "ok\n", **kw) -> Path:
        """検査の出力。 段ごとの記録（steps の中身の 1 段）も、 検査の道具と同じ形で置く。"""
        line = self.line(kw.pop("head", self.head), **kw)
        self.n += 1
        logs = self.base / f"rv-{self.n}" / "logs"
        if steps is not None:
            (logs / "01-basic-checks-checks").mkdir(parents=True)
            (logs / "01-basic-checks-checks" / "03.log").write_text(steps, encoding="utf-8")
        return self.file(f"結果: PASS（略）\n記録の行（PR に書く）:\n{line}\n段ごとの記録: {logs}\n")

    def record(self, **over) -> Path:
        rec = {"rounds": [{"name": "1 回目", "head": self.first, "a": {"result": "FAIL", "blocker": 0, "major": 1, "in_pr": 1},
                           "b": ok("PASS WITH FOLLOW-UP")},
                          {"name": "確かめ直し", "head": self.head, "a": ok(), "b": ok()}],
               "summary": "1 回目の A の MAJOR は、この PR の中で直した。",
               "follow_up": {"external": ["文書の PR を後に merge する"], "backlog": ["場面を足す（#9）"]},
               "human_required": {"applies": False, "reason": "変えたのは試験だけ。",
                                  "readings": {"a": False, "b": False}}}
        rec.update(over)
        return self.file(json.dumps(rec, ensure_ascii=False), ".json")

    def move_main(self) -> None:
        """別の clone から origin の main を 1 つ進める（ほかの Pull Request の merge）。"""
        other = self.base / "other"
        git("clone", "-q", "-b", "main", self.origin, other)
        (other / "m.txt").write_text("x\n", encoding="utf-8")
        git("add", "-A", cwd=other)
        git("commit", "-q", "-m", "m", cwd=other)
        git("push", "-q", "origin", "HEAD:main", cwd=other)

    def pr(self) -> dict:
        return self.db["prs"]["7"]

    def run(self, *argv) -> "tuple[int, str, str]":
        self.db_path.write_text(json.dumps(self.db), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        env = dict(os.environ, PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", FAKE_GH_DB=str(self.db_path),
                   **GIT_ENV)
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = G.main([str(x) for x in argv])
            except SystemExit as e:
                code = e.code
        self.db = json.loads(self.db_path.read_text(encoding="utf-8"))
        return code, out.getvalue(), err.getvalue()

    def gate(self, **kw) -> "tuple[int, str, str]":
        return self.run("gate", self.wt, "--pr", 7, "--rv-log", kw.pop("rv", None) or self.rv(),
                        "--record", self.record(**kw))


class Base(unittest.TestCase):

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.w = World(Path(td.name))

    def assertStops(self, got, word: str, code: int = 1) -> None:
        rc, out, err = got
        self.assertEqual(rc, code, err)
        self.assertEqual(out, "", "止まったときは文を作らない")
        self.assertIn(word, err)

    def tearDown(self) -> None:
        # gh は読むだけ（pr view のほかを呼ばない）
        for call in self.w.db["calls"]:
            self.assertEqual(call[:2], ["pr", "view"], call)


class ReviewTest(Base):

    def test_first_request_follows_the_template(self) -> None:
        rc, out, err = self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv(),
                                  "--look", "特に、試験の待ち方")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out.splitlines(), [
            "Reviewer A（Correctness）として、MirumeAI/development-docs #7（Issue #6）を読み取りだけでレビューしてください。",
            f"- head: {self.w.head}",
            f"- 差分: {self.w.wt} で git diff origin/main...HEAD",
            f"- Required Verification: {self.w.line(self.w.head)}",
            "- 見てほしいこと: Issue の要求との一致・実装の正しさ・回帰・試験・範囲・文書の整合。特に、試験の待ち方",
            "顧客の名前と値は、報告に写さないでください。GitHub への操作は読むだけにしてください。",
            "最後に、見た head、follow-up の 4 つの分類、Result と BLOCKER・MAJOR・IN-PR REQUIRED FIXES の数を出してください。"])

    def test_b_needs_the_authors_reading_and_a_must_not_get_it(self) -> None:
        self.assertStops(self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "b", "--rv-log", self.w.rv()),
                         "--human", 2)
        self.assertStops(self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv(),
                                    "--human", "当たらない"), "--human", 2)
        rc, out, err = self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "b", "--rv-log", self.w.rv(),
                                  "--human", "当たらない（試験だけ）")
        self.assertEqual(rc, 0, err)
        self.assertIn("Reviewer B（Security / Safety）として、", out)
        self.assertIn("人の判断が要る変更に当たるか", out)
        self.assertIn("- 作成者の「人の判断が要る変更」の判定: 当たらない（試験だけ）\n", out)

    def test_recheck_names_the_previous_head_and_fixes(self) -> None:
        rc, out, err = self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv(),
                                  "--prev", self.w.first[:12], "--fixed", "待ち方を直した", "--fixed", "数を直した")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"- 見てほしいこと: {G.RECHECK_LOOK}\n", out)
        rc, out_b, err = self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "b", "--rv-log", self.w.rv(),
                                    "--human", "当たらない", "--prev", self.w.first, "--fixed", "直した")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"- 見てほしいこと: {G.RECHECK_LOOK}・人の判断が要る変更に当たるか\n", out_b)
        self.assertIn(f"- 確かめ直し: 前の head {self.w.first}。変わった所は {self.w.wt} で git diff {self.w.first[:12]}..HEAD\n"
                      "- 直したこと:\n  - 待ち方を直した\n  - 数を直した\n", out)

    def test_review_inputs_may_mention_the_marks_but_not_write_records(self) -> None:
        # 頼む文は GitHub に載せないので、 印の言葉に触れるのはよい。 記録の形（`…:`）は断る（#116）
        base = ("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv())
        rc, out, err = self.w.run(*base, "--look", "Human Verification が要る場面に当たるか")
        self.assertEqual(rc, 0, err)
        self.assertIn("Human Verification が要る場面に当たるか", out)
        rc, out, err = self.w.run(*base, "--prev", self.w.first, "--fixed", "人の判断（Human Adjudication）の言葉の扱いを直した")
        self.assertEqual(rc, 0, err)
        for look in ("Human Verification: PASS", "human  adjudication： MERGE APPROVED", "人の判断（人の言葉のまま）: 「x」"):
            with self.subTest(look=look):
                self.assertStops(self.w.run(*base, "--look", look), "記録の形", 2)
        self.assertStops(self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "b", "--rv-log", self.w.rv(),
                                    "--human", "Human Adjudication: MERGE APPROVED"), "記録の形", 2)

    def test_recheck_needs_a_different_head_and_fixes(self) -> None:
        base = ("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv())
        self.assertStops(self.w.run(*base, "--prev", self.w.head, "--fixed", "x"), "同じ", 2)
        self.assertStops(self.w.run(*base, "--prev", self.w.first), "--fixed", 2)
        self.assertStops(self.w.run(*base, "--fixed", "x"), "--prev", 2)
        self.assertStops(self.w.run(*base, "--prev", "0" * 12, "--fixed", "x"), "ありません", 2)
        self.assertStops(self.w.run(*base, "--prev", self.w.first, "--fixed", "1 行目\n## 見出し"), "1 行", 2)


class RecordLineTest(Base):
    """頼み・門・本文のどれでも、 使えない検査の記録では文を作らない。"""

    def each(self, rv: Path, word: str) -> None:
        for argv in (("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", rv),
                     ("gate", self.w.wt, "--pr", 7, "--rv-log", rv, "--record", self.w.record()),
                     ("body", self.w.wt, "--pr", 7, "--rv-log", rv, "--out", self.w.base / "body.md")):
            with self.subTest(argv[0]):
                self.assertStops(self.w.run(*argv), word)

    def test_stale_record_line(self) -> None:
        self.each(self.w.rv(head=self.w.first), "今の head で検査を流し直して")

    def test_record_without_base(self) -> None:
        self.each(self.w.rv(has="いいえ"), "基準を含みません")

    def test_record_with_other_tool(self) -> None:
        self.each(self.w.rv(same="正本と違う"), "正本と違います")

    def test_record_of_other_repository(self) -> None:
        self.each(self.w.rv(slug="MirumeAI/PDM"), "Repository が Pull Request と違います")

    def test_failed_or_mixed_logs(self) -> None:
        self.each(self.w.file("結果: FAIL（略）\n"), "FAIL")
        self.each(self.w.file(self.w.rv().read_text(encoding="utf-8") + "結果: FAIL（略）\n"), "FAIL")
        two = self.w.rv().read_text(encoding="utf-8") + self.w.rv(head=self.w.first).read_text(encoding="utf-8")
        self.each(self.w.file(two.replace("結果: PASS（略）\n", "")), "2 つ以上")
        self.each(self.w.file(f"{G.RECORD_PASS}（崩れた形）\n"), "形が")

    def test_unpushed_commit_in_worktree(self) -> None:
        self.w.commit("c.txt", "1\n")
        self.each(self.w.rv(head=git("rev-parse", "HEAD", cwd=self.w.wt)), "push していない")

    def test_record_against_other_base(self) -> None:
        self.w.pr()["baseRefName"] = "legacy-line"
        self.assertStops(self.w.run("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a",
                                    "--rv-log", self.w.rv()), "基準が Pull Request の base と違います")

    def test_closed_pull_request(self) -> None:
        self.w.pr()["state"] = "CLOSED"
        self.each(self.w.rv(), "開いていません")


class GateTest(Base):

    def test_gate_record_and_pinned_merge_command(self) -> None:
        rc, out, err = self.w.gate()
        self.assertEqual(rc, 0, err)
        h, f = self.w.head, self.w.first
        self.assertEqual(out, "\n".join([
            "## Merge の門の記録（AUTO MERGE Gate）", "",
            f"- merge する head: `{h}`（基準 origin/main `{self.w.base_sha[:12]}` を含む）",
            f"- Required Verification: {self.w.line(h)}", "",
            "### Reviewer", "",
            "| 回 | 見た head | Reviewer A（Correctness） | Reviewer B（Security / Safety） |",
            "|---|---|---|---|",
            f"| 1 回目 | {f[:12]} | FAIL（BLOCKER 0・MAJOR 1・IN-PR 1） | PASS WITH FOLLOW-UP（BLOCKER 0・MAJOR 0・IN-PR 0） |",
            f"| 確かめ直し | {h[:12]} | PASS（BLOCKER 0・MAJOR 0・IN-PR 0） | PASS（BLOCKER 0・MAJOR 0・IN-PR 0） |", "",
            "1 回目の A の MAJOR は、この PR の中で直した。", "",
            "### follow-up", "",
            "- In-PR Required Fix: なし", "- Post-Merge: なし",
            "- External Task: 文書の PR を後に merge する", "- Backlog: 場面を足す（#9）", "",
            "### 人の判断が要る変更か", "",
            "当たらない（作成者・A・B の読みが同じ）。変えたのは試験だけ。", "",
            "道具が確かめた条件（head の一致・検査の記録・main の進み・Reviewer A・B の最後の結果・In-PR の残り・"
            "人の判断が要る変更の読み）は満たす。道具が見ない条件（development-docs の "
            "`02_github_workflow/pull_request_review.md` の「Merge の門」の一覧。follow-up の判断の中身・"
            "Product Release の手順の間でないか・人の言葉が人自身のものか・渡した記録が報告と人の言葉に合うかなど）と、人の確認が要る場面"
            "（development-docs の `03_customer_development/customer_isolation_check.md` の「要る場面」）に当たらないことを"
            "Agent が確かめてから、head を固定して Squash and merge する（人の確認が要る Pull Request は、この道具の外。"
            "人の確認そのものは人が書く）。", ""]))
        cmd = err.strip().splitlines()[-1]
        self.assertEqual(cmd, f"cd {shlex.quote(str(self.w.wt))} && git fetch -q origin +refs/heads/main:refs/remotes/origin/main"
                              f" && [ \"$(git rev-parse origin/main)\" = \"{self.w.base_sha}\" ]"
                              f" && gh pr ready 7 && gh pr merge 7 --squash --match-head-commit {h}")
        self.w.pr()["isDraft"] = False
        self.assertNotIn("gh pr ready", self.w.gate()[2])

    def test_main_moved_after_verification(self) -> None:
        self.w.move_main()
        self.assertStops(self.w.gate(), "main が検査の基準から進みました")

    def test_record_claiming_a_base_the_head_lacks(self) -> None:
        self.w.move_main()
        git("fetch", "-q", "origin", cwd=self.w.wt)
        self.assertStops(self.w.gate(rv=self.w.rv(base=git("rev-parse", "origin/main", cwd=self.w.wt))),
                         "head が main を含みません")

    def test_merge_command_stops_when_main_moves_later(self) -> None:
        cmd = self.w.gate()[2].strip().splitlines()[-1]
        script = cmd.split(" && gh pr ready")[0]
        env = dict(os.environ, **GIT_ENV)
        self.assertEqual(subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode, 0)
        self.w.move_main()
        self.assertNotEqual(subprocess.run(["bash", "-c", script], env=env, capture_output=True).returncode, 0)

    def test_main_moved_with_narrow_fetch_refspec(self) -> None:
        git("config", "remote.origin.fetch", "+refs/heads/feature/7-x:refs/remotes/origin/feature/7-x", cwd=self.w.wt)
        self.w.move_main()
        self.assertStops(self.w.gate(), "main が検査の基準から進みました")

    def test_human_verification_is_outside_the_tool(self) -> None:
        # 宣言（customer-profile.yaml）を変える Pull Request
        (self.w.wt / "customer-profile.yaml").write_text("x: 1\n", encoding="utf-8")
        git("add", "-A", cwd=self.w.wt)
        git("commit", "-q", "-m", "p", cwd=self.w.wt)
        head = git("rev-parse", "HEAD", cwd=self.w.wt)
        self.w.pr()["headRefOid"] = head
        rounds = [{"name": "1 回目", "head": head, "a": ok(), "b": ok()}]
        self.assertStops(self.w.gate(rv=self.w.rv(head=head), rounds=rounds), "人の確認（Human Verification）が要る")

    def test_profile_expansion_is_outside_the_tool(self) -> None:
        rv = self.w.rv(steps="宣言との照合\nPROFILE EXPANSION DETECTED（宣言が増えています）\n")
        self.assertStops(self.w.gate(rv=rv), "人の確認（Human Verification）が要る")
        self.assertEqual(self.w.gate(rv=self.w.rv(steps=None))[0], 0, "顧客 Repository でなければ、記録が無くても止めない")

    def test_reviewer_must_see_the_merge_head(self) -> None:
        rounds = [{"name": "1 回目", "head": self.w.first, "a": ok(), "b": ok()},
                  {"name": "確かめ直し", "head": self.w.head, "a": ok()}]
        self.assertStops(self.w.gate(rounds=rounds), "Reviewer B の最後の結果が、merge する head を見ていません")
        rounds = [{"name": "1 回目", "head": self.w.head, "a": ok()}]
        self.assertStops(self.w.gate(rounds=rounds), "Reviewer B の結果がありません")
        rounds = [{"name": "1 回目", "head": self.w.head, "a": ok(), "b": ok()},
                  {"name": "確かめ直し", "head": self.w.first, "a": ok(), "b": ok()}]
        self.assertStops(self.w.gate(rounds=rounds), "Reviewer A の最後の結果が")

    def test_nonzero_counts_stop(self) -> None:
        for role in ("a", "b"):
            for res in ({"result": "FAIL", "blocker": 0, "major": 0, "in_pr": 1},
                        {"result": "FAIL", "blocker": 1, "major": 0, "in_pr": 1},
                        {"result": "FAIL", "blocker": 0, "major": 2, "in_pr": 2}):
                with self.subTest(role=role, res=res):
                    r = {"name": "1 回目", "head": self.w.head, "a": ok(), "b": ok(), role: res}
                    self.assertStops(self.w.gate(rounds=[r]), f"Reviewer {role.upper()} の BLOCKER・MAJOR・IN-PR が 0 ではありません")

    def test_miscopied_counts_are_refused(self) -> None:
        for res, word in (({"result": "FAIL", "blocker": 0, "major": 0, "in_pr": 0}, "FAIL は in_pr が 1 以上"),
                          ({"result": "PASS", "blocker": 0, "major": 0, "in_pr": 1}, "FAIL は in_pr が 1 以上"),
                          ({"result": "FAIL", "blocker": 1, "major": 1, "in_pr": 1}, "少ない"),
                          ({"result": "PWF", "blocker": 0, "major": 0, "in_pr": 0}, "略さない"),
                          ({"result": "PASS", "blocker": 0, "major": 0}, "4 つ"),
                          ({"result": "PASS", "blocker": False, "major": 0, "in_pr": 0}, "整数")):
            with self.subTest(res=res):
                r = {"name": "1 回目", "head": self.w.head, "a": res, "b": ok()}
                self.assertStops(self.w.gate(rounds=[r]), word, 2)

    def test_in_pr_follow_up_left(self) -> None:
        self.assertStops(self.w.gate(follow_up={"in_pr": ["直す"]}), "In-PR の直しが残っています")

    def human(self, words: str, **kw) -> dict:
        return {"applies": True, "reason": "守りの境に当たる。", "readings": {"a": True, "b": True},
                "adjudication": {"words": words, "at": "2026-10-27 20:00 JST"}, **kw}

    def test_human_required_needs_words_naming_this_pr_and_head(self) -> None:
        h12, f12 = self.w.head[:12], self.w.first[:12]
        self.assertStops(self.w.gate(human_required={k: v for k, v in self.human("x").items() if k != "adjudication"}),
                         "adjudication", 2)
        # Agent が head を別の欄に書いて、 人が名指ししたことにする道は無い
        hr = self.human("development-docs #7 の merge を承認します")
        hr["adjudication"]["head"] = self.w.head
        self.assertStops(self.w.gate(human_required=hr), "adjudication は words", 2)
        for words, why in (("1 で進めてください", "#番号"),
                           ("development-docs #7 の merge を承認します", "head（12 桁以上）がありません"),
                           (f"development-docs #7（head {f12}）の merge を承認します", "merge する head と違います"),
                           (f"development-docs #7（head {h12}）と #8 の merge を承認します", "#番号"),
                           (f"development-docs #8（head {h12}）の merge を承認します", "#番号"),
                           (f"PDM #7（head {h12}）の merge を承認します", "別の Repository"),
                           (f"development-docs #7（head {h12}・{f12}）の merge を承認します", "merge する head と違います")):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), why)
        words = f"development-docs #7（head {h12}）の merge を承認します"
        rc, out, err = self.w.gate(human_required=self.human(words))
        self.assertEqual(rc, 0, err)
        self.assertIn(f"当たる（作成者・A・B の読みが同じ）。守りの境に当たる。\n\n人の判断（人の言葉のまま）: 「{words}」"
                      f"（2026-10-27 20:00 JST）。言葉が名指しした Pull Request #7 と head `{h12}` は、merge する head と同じ"
                      "（判断の後に head は変わっていない）。\n", out)
        rc, out, err = self.w.gate(human_required=self.human(f"＃7（head {self.w.head}）の merge を承認します"))
        self.assertEqual(rc, 0, err)
        self.assertStops(self.w.gate(human_required={"applies": False, "reason": "x", "readings": {"a": False, "b": False},
                                                     "adjudication": {"words": words, "at": "x"}}), "readings（当たる", 2)

    def test_other_repository_name_next_to_japanese(self) -> None:
        # 名前のすぐ後に和文が続いても、 別の Repository の名指しを見逃さない（#107）
        h12 = self.w.head[:12]
        for words in (f"PDMの #7（head {h12}）の merge を承認します",
                      f"Image-Acquisition-PDMは #7（head {h12}）で承認します",
                      f"#7（head {h12}）は.githubで承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "別の Repository")
        rc, _, err = self.w.gate(human_required=self.human(f"development-docsの #7（head {h12}）の merge を承認します"))
        self.assertEqual(rc, 0, err)

    def test_short_heads_must_be_the_merge_head(self) -> None:
        # 12 桁より短い別の head を並べた言葉は引かない。 数字だけの並び（日付など）は head として見ない（#107）
        h, h12 = self.w.head, self.w.head[:12]
        other = next(x for x in ("abcdef0", "fedcba1", "0a0a0a0") if not h.startswith(x))
        self.assertStops(self.w.gate(human_required=self.human(f"#7（head {h12}、前の {other}）の merge を承認します")),
                         "短い head")
        self.assertStops(self.w.gate(human_required=self.human(f"#7（head {h12}・{other}aa）の merge を承認します")),
                         "短い head")
        for words in (f"#7（head {h12}）の merge を 20261028 に承認します",
                      f"#7（head {h12}、短く {h[:8]}a）の merge を承認します" if h[8] == "a" else f"#7（head {h12}、短く {h[:9]}）を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_short_head_boundaries(self) -> None:
        # 道具が見分けるのは a〜f を含む 7〜11 桁。 11 桁の別の head は止め、 6 桁以下は見分けない（#109）
        h, h12 = self.w.head, self.w.head[:12]
        other11 = next(x for x in ("abcdef01234", "fedcba98765") if not h.startswith(x))
        self.assertStops(self.w.gate(human_required=self.human(f"#7（head {h12}、前の {other11}）を承認します")), "短い head")
        rc, _, err = self.w.gate(human_required=self.human(f"#7（head {h12}、印 abcdef）を承認します"))
        self.assertEqual(rc, 0, err)
        # 今の head の先頭（a〜f を含む 7〜11 桁）は通る
        k = next((k for k in range(7, 12) if any(c in "abcdef" for c in h[:k])), None)
        if k is not None:
            rc, _, err = self.w.gate(human_required=self.human(f"#7（head {h12}、短く {h[:k]}）を承認します"))
            self.assertEqual(rc, 0, err)

    def test_case_width_and_number_forms(self) -> None:
        # 大文字・全角の 16 進と名前、 全角の数字、 # の無い番号も照らす（#109）
        h, h12 = self.w.head, self.w.head[:12]
        other = next(x for x in ("abcdef0", "fedcba1") if not h.startswith(x))
        full = "".join(chr(ord(c) + 0xFEE0) for c in h12)              # 全角の英数字
        for words, why in ((f"#7（head {h12}・{other.upper()}）を承認します", "短い head"),
                           (f"#7（head {h12}・{('0' * 12).upper() if h12 != '0' * 12 else 'f' * 12}）を承認します",
                            "merge する head と違います"),
                           (f"#7（head {h12}）と ＃８ を承認します", "#番号"),
                           (f"#7（head {h12}）と PR 8 を承認します", "#番号"),
                           (f"#7（head {h12}）と Pull Request #8 を承認します", "#番号"),
                           (f"pdm の #7（head {h12}）を承認します", "別の Repository"),
                           (f"ＰＤＭ の #7（head {h12}）を承認します", "別の Repository")):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), why)
        for words in (f"＃７（head {full}）を承認します", f"PR 7（head {h12.upper()}）を承認します",
                      f"Development-Docs #7（head {h12}）を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_human_verification_is_a_mark_too(self) -> None:
        for text in ("直した。\n\nHuman Verification: PASS", "直した。 HUMAN  VERIFICATION: PASS",
                     "直した。 Ｈｕｍａｎ Ｖｅｒｉｆｉｃａｔｉｏｎ"):
            with self.subTest(text=text):
                self.assertStops(self.w.gate(summary=text), "人の確認（Human Verification）", 2)
        self.assertStops(self.w.gate(summary="直した。 human adjudication: x"), "人の判断の印", 2)
        h12 = self.w.head[:12]
        self.assertStops(self.w.gate(human_required=self.human(f"Human Verification: PASS #7 head {h12}")),
                         "人の確認（Human Verification）")
        self.assertStops(self.w.gate(human_required=self.human(f"Human Adjudication: MERGE APPROVED #7 head {h12}")),
                         "コメントの形")

    def test_number_forms_without_hash(self) -> None:
        # # の無い番号の形（Pull Request・小文字・プルリクエスト）と、 並べた番号も数える（#109）
        h12 = self.w.head[:12]
        for words in (f"#7（head {h12}）と Pull Request 8 を承認します", f"#7（head {h12}）と pr 8 を承認します",
                      f"#7（head {h12}）とプルリクエスト 8 を承認します", f"#7・8（head {h12}）を承認します",
                      f"PR 7, 8（head {h12}）を承認します", f"#7 と 8（head {h12}）を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")
        for words in (f"Pull Request 7（head {h12}）を承認します", f"プルリクエスト #7（head {h12}）を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_head_or_date_after_a_separator_is_not_a_number(self) -> None:
        # 区切りの後ろに head や日付を書いた正しい言葉は止めない。 3 つ並べた番号は止める（#112 の Reviewer A）
        h12 = self.w.head[:12]
        for words in (f"#7、{h12} の merge を承認します", f"#7・{h12} を承認します", f"#7, {h12} approved",
                      f"#7 と {h12} を承認します", f"#7/{h12} を承認します", f"#7、2026-10-28 に head {h12} を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)
        self.assertStops(self.w.gate(human_required=self.human(f"#7・8・9（head {h12}）を承認します")), "#番号")
        self.assertStops(self.w.gate(human_required=self.human(f"#9・8・7（head {h12}）を承認します")), "#番号")

    def test_more_number_forms_and_separators(self) -> None:
        # まだ数えなかった番号の形と並べ方も数える（#116）
        h12 = self.w.head[:12]
        for words in (f"#7（head {h12}）と PR-8 を承認します", f"#7（head {h12}）と Pull  Request 8 を承認します",
                      f"#7（head {h12}）と PullRequest 8 を承認します", f"#7（head {h12}）とプルリク 8 を承認します",
                      f"#7（head {h12}）と №8 を承認します",
                      f"#7（head {h12}）と https://github.com/MirumeAI/development-docs/pull/8 を承認します",
                      f"#7 and 8（head {h12}）を承認します", f"#7〜9（head {h12}）を承認します",
                      f"#7～9（head {h12}）を承認します", f"#7-8（head {h12}）を承認します",
                      f"#7-8-9（head {h12}）を承認します", f"#7 や 8（head {h12}）を承認します",
                      f"#7 & 8（head {h12}）を承認します", f"#7から9（head {h12}）を承認します",
                      f"#7/8（head {h12}）を承認します", f"#7・7・9（head {h12}）を承認します",
                      f"#7・123456（head {h12}）を承認します", f"#7（head {h12}）と #\u200b8 を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")
        for words in (f"https://github.com/MirumeAI/development-docs/pull/7（head {h12}）を承認します",
                      f"PR-7（head {h12}）を承認します", f"#7・1234567（head {h12}）を承認します",
                      f"#7（head {h12[:6]}\u200b{h12[6:]}）を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_numbers_after_a_prefix_or_a_slash_are_not_dates(self) -> None:
        # 番号の前置きの直後と / で続く番号は、 日付の形でも番号に数える（#124 の Reviewer B の MAJOR-1）
        h12 = self.w.head[:12]
        for words in (f"#7 と PR 8/9（head {h12}）を承認します", f"PR 8/9 と #7（head {h12}）の merge を承認します",
                      f"Pull Request 8/9 と #7（head {h12}）を承認します", f"#7/8/9（head {h12}）を承認します",
                      f"#7（head {h12}）と プルリク 10:30 を承認します", f"#7・13/14（head {h12}）を承認します",
                      f"#7、25:61（head {h12}）を承認します", f"#7 と №8/9（head {h12}）を承認します",
                      f"#7-10-28（head {h12}）を承認します", f"#7〜10/28（head {h12}）を承認します",
                      f"#7・10/28・9（head {h12}）を承認します", f"#7・10:30 と 8（head {h12}）を承認します",
                      f"#7・2026-10-28・8（head {h12}）を承認します", f"#7・8/9/10（head {h12}）を承認します",
                      f"#7・8日 と 9（head {h12}）を承認します", f"#7、8-9（head {h12}）を承認します",
                      f"#7・2026年10月28日 と 8（head {h12}）を承認します", f"#7・10時30分・8（head {h12}）を承認します",
                      f"#7・123日（head {h12}）を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")

    def test_more_range_and_prefix_forms(self) -> None:
        # 範囲の字（– — −）・or・または・PRs・Pull Requests・pull-request・No. も数える（#124 の Reviewer A）
        h12 = self.w.head[:12]
        for words in (f"#7–9（head {h12}）を承認します", f"#7—9（head {h12}）を承認します", f"#7−9（head {h12}）を承認します",
                      f"#7 or 8（head {h12}）を承認します", f"#7 または 8（head {h12}）を承認します",
                      f"#7（head {h12}）と PRs 8 and 9 を承認します", f"#7（head {h12}）と Pull Requests 8 を承認します",
                      f"#7（head {h12}）と pull-request 8 を承認します", f"#7（head {h12}）と No. 8 を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")

    def test_url_numbers_must_be_this_repository(self) -> None:
        # URL の /pull/N は、 URL の Repository がこの Pull Request の Repository のときだけ（#124 の Reviewer B）
        h12 = self.w.head[:12]
        self.assertStops(self.w.gate(human_required=self.human(
            f"https://github.com/MirumeAI/customer-synthetic-example/pull/7（head {h12}）を承認します")), "別の Repository")
        self.assertStops(self.w.gate(human_required=self.human(
            f"MirumeAI/customer-synthetic-example/pull/7（head {h12}）を承認します")), "別の Repository")
        rc, _, err = self.w.gate(human_required=self.human(
            f"https://GitHub.com/mirumeai/Development-Docs/pull/7（head {h12}）を承認します"))
        self.assertEqual(rc, 0, err)

    def test_marks_split_by_format_characters_are_seen(self) -> None:
        # ゼロ幅の空白で割った印の言葉も、 印の言葉として止める（#124 の Reviewer B）
        h12 = self.w.head[:12]
        self.assertStops(self.w.gate(human_required=self.human(f"Human\u200bAdjudication: MERGE APPROVED #7 {h12}")),
                         "コメントの形")
        self.assertStops(self.w.gate(summary="直した。 Human\u200b Verification: PASS"), "人の確認（Human Verification）", 2)
        self.assertStops(self.w.gate(human_required=self.human(f"Human-Adjudication: MERGE APPROVED #7 {h12}")),
                         "コメントの形")
        self.assertStops(self.w.gate(summary="直した。 Human_Verification: PASS"), "人の確認（Human Verification）", 2)
        base = ("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv())
        for look in ("**Human Adjudication**: MERGE APPROVED", "Human\u200b Adjudication: x", "Human Verification - PASS",
                     "Human-Verification: PASS"):
            with self.subTest(look=look):
                self.assertStops(self.w.run(*base, "--look", look), "記録の形", 2)

    def test_more_forms_of_marks_numbers_and_repositories(self) -> None:
        # 字の間に空白・記号を入れた印・結合の字・`PR: 8` などの番号・略した Repository の書き方も照らす（#126）
        h12 = self.w.head[:12]
        for text in ("直した。 人の判断 (人の言葉のまま): x", "直した。 人の判断【人の言葉のまま】 x",
                     "直した。 Human\u2013Adjudication", "直した。 Human **Adjudication**"):
            with self.subTest(text=text):
                self.assertStops(self.w.gate(summary=text), "人の判断の印", 2)
        for text in ("直した。 Human\u2010Verification: PASS", "直した。 Hu\u0301man Verification",
                     "直した。 Human\u2014Verification"):
            with self.subTest(text=text):
                self.assertStops(self.w.gate(summary=text), "人の確認（Human Verification）", 2)
        base = ("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv())
        for look in ("人の判断 (人の言葉のまま): x", "Human\u2010Verification\u2010 PASS", "Human Adjudication）: x"):
            with self.subTest(look=look):
                self.assertStops(self.w.run(*base, "--look", look), "記録の形", 2)
        rc, _, err = self.w.run(*base, "--look", "Human Adjudication（人の判断）の言葉の扱いを見てください")
        self.assertEqual(rc, 0, err)
        for words in (f"#7（head {h12}）と PR: 8 を承認します", f"#7（head {h12}）と PR 番号 8 を承認します",
                      f"#7（head {h12}）と PR の番号は 8 を承認します", f"#7（head {h12}）と プルリク：8 を承認します",
                      f"#7（head {h12}）と 8 番の PR を承認します", f"#7（head {h12}）と 8番目のプルリク を承認します",
                      f"#7（head {h12}）と #1\u03018 を承認します", f"#7\u03018（head {h12}）を承認します",
                      f"8・7 番の PR（head {h12}）を承認します", f"8 と 7 番の PR（head {h12}）を承認します",
                      f"8、7 番目のプルリク（head {h12}）を承認します", f"7 番の PRESET（head {h12}）を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")
        for words in (f"MirumeAI/customer-synthetic-example#7（head {h12}）を承認します",
                      f"MirumeAI/customer-synthetic-example #7（head {h12}）を承認します",
                      f"mirumeai/customer-synthetic-example の #7（head {h12}）を承認します",
                      f"other/development-docs#7（head {h12}）を承認します",
                      f"other/development-docs #7（head {h12}）を承認します",
                      f"#7（head {h12}）と éPDM の件を承認します",
                      f"ＭｉｒｕｍｅＡＩ／ｃｕｓｔｏｍｅｒ＃7（head {h12}）を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "別の Repository")
        for words in (f"MirumeAI/development-docs#7（head {h12}）を承認します",
                      f"mirumeai/Development-Docs #7（head {h12}）を承認します。",
                      f"MirumeAI/development-docs. の #7（head {h12}）を承認します",
                      f"PR: 7（head {h12}）を承認します", f"7 番の PR（head {h12}）を承認します",
                      f"#7（head {h12}）を 2 番目の案で承認します", f"10/28 #7（head {h12}）を承認します",
                      f"2026/10/28 #7（head {h12}）を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_a_letter_with_a_mark_keeps_the_boundary(self) -> None:
        # 結合の字を外すと `ö` が `o` になり、 すぐ後の別の head を見逃す。 外さない文でも照らす（#127 の Reviewer B）
        h, h12 = self.w.head, self.w.head[:12]
        other = next(x for x in ("abcdef012345", "fedcba987654") if not h.startswith(x))
        for words in (f"#7（head {h12}）と ö{other} を承認します", f"#7（head {h12}）と o\u0308{other} を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "merge する head と違います")

    def test_long_spaces_do_not_slow_the_check(self) -> None:
        # 番号の前置きの後に長い空白が続いても、 照らしは遅くならない（#127 の Reviewer A・B）。
        # 遅い形は止まらなくなるので、 子の処理で流して時間で切る
        code = ("import importlib.util, sys, types\n"
                f"spec = importlib.util.spec_from_file_location('g', {str(HERE / 'gate_record.py')!r})\n"
                "g = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(g)\n"
                "ctx = types.SimpleNamespace(slug='MirumeAI/development-docs', number=7, head='a' * 40)\n"
                "for w in sys.argv[1:]:\n"
                "    try:\n"
                "        g.named_head(ctx, w)\n"
                "    except g.Stop:\n"
                "        pass\n")
        sp = " " * 3000
        words = [f"PR{sp}x", f"No.{sp}x", f"Pull Request{sp}の{sp}番号{sp}x", f"プルリク{sp}#{sp}x",
                 f"8{sp}番{sp}の{sp}x", f"#7（head {'a' * 12}）PR{sp}"]
        try:
            r = subprocess.run([sys.executable, "-c", code, *words], capture_output=True, text=True, timeout=20)
        except subprocess.TimeoutExpired:
            self.fail("長い空白で、 番号の照らしが 20 秒で終わらない")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_ambiguous_forms_stay_refused(self) -> None:
        # 形だけで番号か日付・項目の番号かを決められないものは、 断る側に置く（#126。 人が言い直せば通る）。
        # つなぐ区切り（`–`・`/`）の後ろは、 `#7–10`・`#7/10` の番号の並びと見分けられない。 `No.` は Pull Request の番号にも使う
        h12 = self.w.head[:12]
        for words in (f"#7 – 10/28（head {h12}）を承認します", f"#7 / 10/28（head {h12}）を承認します",
                      f"#7（head {h12}）を承認します（No.1 の件）"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "#番号")

    def test_dates_and_times_after_a_separator_are_not_numbers(self) -> None:
        # 区切りの後ろの日付と時刻で止めすぎない（#116）
        h12 = self.w.head[:12]
        for words in (f"#7・10/28（head {h12}）を承認します", f"#7、10:30 に head {h12} を承認します",
                      f"#7・10月28日に head {h12} を承認します", f"#7（head {h12}）2026/10/28 承認",
                      f"#7、2026.10.28 に head {h12} を承認します", f"#7 と 2026年10月28日 に head {h12} を承認します",
                      f"#7・2026-10 に head {h12} を承認します"):
            with self.subTest(words=words):
                rc, _, err = self.w.gate(human_required=self.human(words))
                self.assertEqual(rc, 0, err)

    def test_a_short_head_next_to_a_fullwidth_letter_is_seen(self) -> None:
        # 全角の字が隣にある短い別の head も、 元の文で見分けて止める（#116）
        h, h12 = self.w.head, self.w.head[:12]
        other = next(x for x in ("abcdef0", "fedcba1") if not h.startswith(x))
        self.assertStops(self.w.gate(human_required=self.human(f"#7（head {h12}・{other}ｇ）を承認します")), "短い head")

    def test_both_the_original_and_the_normalized_words_are_checked(self) -> None:
        # 全角の字が隣にあって、 そろえた文だけでは見えなくなる並びも止める（#109）
        h, h12 = self.w.head, self.w.head[:12]
        other = next(x for x in ("abcdef012345", "fedcba987654") if not h.startswith(x))
        for words in (f"#7（head {h12}・{other}ｇ）を承認します", f"PDM１ の #7（head {h12}）を承認します",
                      f"#7（head {h12}・{other.upper()}）を承認します"):
            with self.subTest(words=words):
                self.assertStops(self.w.gate(human_required=self.human(words)), "")

    def test_comment_form_adjudication_is_outside_the_tool(self) -> None:
        h = self.w.head
        self.assertStops(self.w.gate(human_required=self.human(f"Human Adjudication: MERGE APPROVED\n\n対象: {h}")), "1 行", 2)
        self.assertStops(self.w.gate(human_required=self.human(f"Human Adjudication: MERGE APPROVED 対象: {h}")),
                         "決まりの形で手で書く")

    def test_skipped_tests_need_a_checked_reason(self) -> None:
        rv = self.w.rv(skipped=3)
        self.assertStops(self.w.gate(rv=rv), "飛ばした試験が 3 件")
        rc, out, err = self.w.gate(rv=self.w.rv(skipped=3), skipped_reason="PyYAML の無い段の 3 件。試験の中身は別の段で流れた")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"- Required Verification: {self.w.line(self.w.head, skipped=3)}\n"
                      "- 飛ばした試験 3 件の理由（Agent が確かめた）: PyYAML の無い段の 3 件。試験の中身は別の段で流れた\n", out)
        self.assertStops(self.w.gate(skipped_reason="理由"), "0 件なのに", 2)
        self.assertStops(self.w.gate(rv=self.w.rv(skipped=1), skipped_reason="1 行目\n2 行目"), "1 行", 2)
        self.assertStops(self.w.gate(rv=self.w.rv(skipped=1), skipped_reason="Human Adjudication: x"), "印の言葉", 2)
        bare = self.w.line(self.w.head).replace("・飛ばした試験 0 件", "")
        self.assertStops(self.w.gate(rv=self.w.file(f"結果: PASS（略）\n{bare}\n")), "飛ばした試験の件数がありません")

    def test_readings_must_agree(self) -> None:
        for applies, rd in ((False, {"a": False, "b": True}), (False, {"a": True, "b": False}),
                            (True, {"a": True, "b": False})):
            with self.subTest(applies=applies, rd=rd):
                hr = self.human(f"development-docs #7（head {self.w.head[:12]}）の merge を承認します") if applies \
                    else {"applies": False, "reason": "x", "readings": {}}
                hr["readings"] = rd
                self.assertStops(self.w.gate(human_required=hr), "読みが、作成者と Reviewer で分かれています")
        self.assertStops(self.w.gate(human_required={"applies": False, "reason": "x", "readings": {"a": False}}),
                         "readings", 2)

    def test_marks_of_human_judgment_only_in_adjudication(self) -> None:
        mark = "人の判断（人の言葉のまま）: 「承認します」"
        self.assertStops(self.w.gate(summary=f"直した。\n\n{mark}"), "印の言葉", 2)
        self.assertStops(self.w.gate(human_required={"applies": False, "reason": f"x。{mark}",
                                                     "readings": {"a": False, "b": False}}), "印の言葉", 2)
        self.assertStops(self.w.gate(human_required={"applies": False, "reason": "1 行目\n2 行目",
                                                     "readings": {"a": False, "b": False}}), "1 行", 2)
        self.assertStops(self.w.gate(follow_up={"backlog": ["Human Adjudication: MERGE APPROVED"]}), "印の言葉", 2)

    def test_only_main_base_and_open_pull_request(self) -> None:
        self.w.pr()["baseRefName"] = "legacy-line"
        self.assertStops(self.w.gate(rv=self.w.rv()), "base が main でない")

    def test_record_shape_is_checked(self) -> None:
        self.assertStops(self.w.gate(extra=1), "rounds・human_required", 2)
        self.assertStops(self.w.gate(rounds=[]), "1 回以上", 2)
        self.assertStops(self.w.gate(rounds=[{"name": "a|b", "head": self.w.head, "a": ok(), "b": ok()}]), "|", 2)
        self.assertStops(self.w.gate(follow_up={"later": []}), "follow_up", 2)
        self.assertStops(self.w.gate(rounds=[{"name": "1", "head": "zz", "a": ok(), "b": ok()}]), "commit", 2)


class MergedAndBodyTest(Base):

    def test_merged_comment(self) -> None:
        h = self.w.head
        self.assertStops(self.w.run("merged", self.w.wt, "--pr", 7, "--head", h), "まだ merge されていません")
        self.w.pr().update(state="MERGED", mergeCommit={"oid": "c" * 40})
        rc, out, err = self.w.run("merged", self.w.wt, "--pr", 7, "--head", h, "--note", "片付けます。")
        self.assertEqual(rc, 0, err)
        self.assertEqual(out, f"merge しました（Squash、merge commit `{'c' * 40}`、固定した head `{h}`）。片付けます。\n")
        self.assertStops(self.w.run("merged", self.w.wt, "--pr", 7, "--head", self.w.first), "固定した head と違います")
        self.assertStops(self.w.run("merged", self.w.wt, "--pr", 7, "--head", h[:12]), "40 桁", 2)

    def test_body_replaces_the_one_record_line(self) -> None:
        out_file = self.w.base / "body.md"
        rc, out, err = self.w.run("body", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(), "--out", out_file)
        self.assertEqual(rc, 0, err)
        old = self.w.pr()["body"]
        new = out_file.read_bytes().decode("utf-8")
        self.assertEqual(new, old.replace(self.w.line(self.w.first), self.w.line(self.w.head)))
        self.assertNotEqual(new, old)
        self.assertEqual(stat.S_IMODE(out_file.stat().st_mode), 0o600)

    def test_body_with_zero_or_two_record_lines(self) -> None:
        line = self.w.line(self.w.first)
        for body in ("本文\n", f"{line}\n{line}\n"):
            with self.subTest(body=body[:10]):
                self.w.pr()["body"] = body
                self.assertStops(self.w.run("body", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(),
                                            "--out", self.w.base / "body.md"), "記録の行が")


class CustomerRepositoryTest(unittest.TestCase):
    """顧客 Repository の名前を画面に出さない。"""

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.w = World(Path(td.name), repo=SECRET, wt_name=f"{SECRET}-wt")

    def test_text_goes_only_to_the_file(self) -> None:
        self.assertIn(SECRET, str(self.w.wt))
        rc, out, err = self.w.gate()
        self.assertEqual(rc, 2)
        self.assertIn("--out", err)
        self.assertNotIn(SECRET, out + err)
        target = self.w.base / "gate.md"
        rc, out, err = self.w.run("gate", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(), "--record", self.w.record(),
                                  "--out", target)
        self.assertEqual(rc, 0, err)
        self.assertNotIn(SECRET, out + err)
        self.assertIn(SECRET, target.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        # merge の命令（作業ツリーの道を含む）も、 画面でなくファイルへ
        cmd = Path(f"{target}.merge")
        self.assertIn(f"--match-head-commit {self.w.head}", cmd.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(cmd.stat().st_mode), 0o600)
        for argv in (("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log", self.w.rv()),
                     ("merged", self.w.wt, "--pr", 7, "--head", self.w.head)):
            with self.subTest(argv[0]):
                rc, out, err = self.w.run(*argv, "--out", self.w.base / "o.md")
                self.assertNotIn(SECRET, out + err)

    def test_existing_out_file_becomes_private(self) -> None:
        target = self.w.base / "gate.md"
        target.write_text("前の中身\n", encoding="utf-8")
        target.chmod(0o644)
        rc, _, err = self.w.run("gate", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(), "--record", self.w.record(),
                                "--out", target)
        self.assertEqual(rc, 0, err)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_stops_do_not_show_the_name(self) -> None:
        cases = [("gate", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(head=self.w.first), "--record", self.w.record(),
                  "--out", self.w.base / "o.md"),
                 ("review", self.w.wt, "--pr", 7, "--issue", 6, "--role", "a", "--rv-log",
                  self.w.rv(slug="MirumeAI/PDM"), "--out", self.w.base / "o.md"),
                 ("gate", self.w.wt, "--pr", 8, "--rv-log", self.w.rv(), "--record", self.w.record(),
                  "--out", self.w.base / "o.md")]
        for argv in cases:
            with self.subTest(argv[0]):
                rc, out, err = self.w.run(*argv)
                self.assertIn(rc, (1, 2))
                self.assertNotIn(SECRET, out + err)
                self.assertNotIn("alpha", out + err)

    def test_missing_step_logs_stop(self) -> None:
        rc, out, err = self.w.run("gate", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(steps=None),
                                  "--record", self.w.record(), "--out", self.w.base / "o.md")
        self.assertEqual(rc, 1, err)
        self.assertIn("段ごとの記録が読めない", err)
        self.assertNotIn(SECRET, out + err)

    def tearDown(self) -> None:
        for call in self.w.db["calls"]:
            self.assertEqual(call[:2], ["pr", "view"], call)

    def test_out_does_not_follow_a_link(self) -> None:
        real = self.w.base / "real.md"
        real.write_text("前の中身\n", encoding="utf-8")
        link = self.w.base / "link.md"
        link.symlink_to(real)
        rc, _, err = self.w.run("gate", self.w.wt, "--pr", 7, "--rv-log", self.w.rv(), "--record", self.w.record(),
                                "--out", link)
        self.assertEqual(rc, 2, err)
        self.assertEqual(real.read_text(encoding="utf-8"), "前の中身\n")


if __name__ == "__main__":
    unittest.main()

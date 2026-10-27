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

    def __init__(self, base: Path, repo: str = "development-docs") -> None:
        self.base = base
        self.slug = f"MirumeAI/{repo}"
        self.origin = base / "origin.git"
        self.wt = base / "wt"
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
             slug: "str | None" = None) -> str:
        return (f"{G.RECORD_PASS}（{slug or self.slug} head {head[:12]}・基準 origin/main {(base or self.base_sha)[:12]}"
                f" を含む: {has}・正本の道具 MirumeAI/.github origin/main {'a' * 12}・道具 {'b' * 12}（{same}）"
                "・workflow 1・job 2・段 13・試験 320 件・飛ばした試験 0 件・19 秒）")

    def file(self, text: str, suffix: str = ".txt") -> Path:
        self.n += 1
        p = self.base / f"f{self.n}{suffix}"
        p.write_text(text, encoding="utf-8")
        return p

    def rv(self, **kw) -> Path:
        line = self.line(kw.pop("head", self.head), **kw)
        return self.file(f"結果: PASS（略）\n記録の行（PR に書く）:\n{line}\n段ごとの記録: /x\n")

    def record(self, **over) -> Path:
        rec = {"rounds": [{"name": "1 回目", "head": self.first, "a": {"result": "FAIL", "blocker": 0, "major": 1, "in_pr": 1},
                           "b": ok("PASS WITH FOLLOW-UP")},
                          {"name": "確かめ直し", "head": self.head, "a": ok(), "b": ok()}],
               "summary": "1 回目の A の MAJOR は、この PR の中で直した。",
               "follow_up": {"external": ["文書の PR を後に merge する"], "backlog": ["場面を足す（#9）"]},
               "human_required": {"applies": False, "reason": "変えたのは試験だけ。"}}
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
        self.assertIn(f"- 確かめ直し: 前の head {self.w.first}。変わった所は {self.w.wt} で git diff {self.w.first[:12]}..HEAD\n"
                      "- 直したこと:\n  - 待ち方を直した\n  - 数を直した\n", out)

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
            "当たらない。変えたのは試験だけ。", "",
            "以上で門の条件を満たすので、Agent が head を固定して Squash and merge する。", ""]))
        cmd = err.strip().splitlines()[-1]
        self.assertEqual(cmd, f"cd {shlex.quote(str(self.w.wt))} && git fetch -q origin main"
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

    def test_human_required_needs_adjudication_on_this_head(self) -> None:
        hr = {"applies": True, "reason": "守りの境に当たる。"}
        self.assertStops(self.w.gate(human_required=hr), "adjudication", 2)
        adj = {"words": "development-docs #7（head x）の merge を承認します", "at": "2026-10-27 20:00 JST",
               "head": self.w.first}
        self.assertStops(self.w.gate(human_required={**hr, "adjudication": adj}), "人の判断の後に head が変わりました")
        rc, out, err = self.w.gate(human_required={**hr, "adjudication": {**adj, "head": self.w.head[:12]}})
        self.assertEqual(rc, 0, err)
        self.assertIn(f"当たる。守りの境に当たる。\n\n人の判断（人の言葉のまま）: 「{adj['words']}」"
                      f"（2026-10-27 20:00 JST、名指しした head `{self.w.head[:12]}`）。判断の後に head は変わっていない。\n", out)
        self.assertStops(self.w.gate(human_required={"applies": False, "reason": "x", "adjudication": adj}),
                         "applies・reason", 2)

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
        self.w = World(Path(td.name), repo=SECRET)

    def test_text_goes_only_to_the_file(self) -> None:
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

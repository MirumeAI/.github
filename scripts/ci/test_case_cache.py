#!/usr/bin/env python3
"""`case_cache.py`（案件のフォルダの `.cache/` と共通の道具の cache を片付ける道具）のテスト。

守ること:
  ・見るだけ（既定）では何も消さない。 `--apply` でも、 消してよい記録とその作業の場所だけを消す
  ・消してよい記録は、 head の Pull Request が merge された物と、 開いた Pull Request の今の head でない物だけ。
    開いた Pull Request の今の head・閉じただけの Pull Request（実物の GitHub は空の答えを返す）・Pull Request が
    見つからない・gh が使えない・gh の答えの形が違う・頭が読めない記録は残す
  ・作業の場所は、 利用者の cache の下の `mirumeai/w/<数>`・`mirumeai/sw/<名前>` の、 この利用者の dir で、
    中の `logs/summary.json` が記録と合うときだけ消す。 残す記録も名指しする場所は消さない
  ・symlink を辿らない（`logs/summary.json` も）。 この利用者の物でない物を消さない。 Required Verification が
    流れている間は、 終わりの行の無い記録を消さない
  ・見てから消すまでの間に入れ替わった物（記録・付属のファイル・作業の場所）と、 中に本人が入れない dir・ほかの
    利用者の dir・別の file system がある作業の場所は、 消さずに誤り（終了コード 1）。 ほかの記録は続けて扱い、
    消し終えた物だけを数える
  ・決まりの一覧に無い物・置かない物（git・仮想環境）は知らせるだけ。 作業ルートなど `.cache/` の外の物に触れない
  ・gh は読むだけ（`api --method GET …/commits/<sha>/pulls` のほかを呼ばない）
  ・出力に Repository の名前（owner/repo）・gh の誤りの文・案件のフォルダと利用者の cache の道・置き場の外の道を
    出さない（記録のファイルの名前と `.cache/` の下の名前は出す。 道具の説明のとおり）

GitHub は gh の代わりの小さな道具でまねる（実際の GitHub に問わない）。 名前はどれも架空。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("case_cache", HERE / "case_cache.py")
C = importlib.util.module_from_spec(_spec)
sys.modules["case_cache"] = C                   # dataclass が自分の module を引くため
_spec.loader.exec_module(C)

SLUG = "ExampleOrg/tool-a"
#: 架空の顧客 Repository の名前（出力に出てはいけない）
SECRET_SLUG = "ExampleOrg/customer-zeta-part"

FAKE_GH = r'''
import json, os, sys
path = os.environ["FAKE_GH_DB"]
with open(path, encoding="utf-8") as f:
    db = json.load(f)
a = sys.argv[1:]
db["calls"].append(a)
with open(path, "w", encoding="utf-8") as f:
    json.dump(db, f)
if len(a) == 4 and a[:3] == ["api", "--method", "GET"]:
    ans = db["answers"].get(a[3])
    if ans is None:
        sys.stderr.write("gh: No commit found for SHA: x (HTTP 422)\n")
        sys.exit(1)
    if isinstance(ans, dict) and "error" in ans:
        sys.stderr.write(ans["error"] + "\n")
        sys.exit(1)
    print(json.dumps(ans))
    sys.exit(0)
sys.stderr.write("fake gh: unknown\n")
sys.exit(2)
'''


def sha(n: int) -> str:
    return hashlib.sha1(str(n).encode()).hexdigest()


class World:
    """案件のフォルダ・利用者の cache・gh の代わりを、 一時の dir に作る。"""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.case = base / "case-a"
        self.rv = self.case / ".cache" / "rv-logs"
        self.rv.mkdir(parents=True)
        self.cache = base / "ucache"
        (self.cache / "mirumeai" / "w").mkdir(parents=True)
        (self.cache / "mirumeai" / "sw").mkdir()
        self.bin = base / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
        gh.chmod(0o755)
        self.db_path = base / "gh.json"
        self.db = {"calls": [], "answers": {}}
        self.running = False
        self.readonly = []              # 片付けの前に権限を戻す dir

    def work(self, name: str, head: str, slug: str = SLUG, kind: str = "w", summary: bool = True) -> Path:
        """検査の道具と同じ形の作業の場所（段ごとの記録・残った clone・書けない dir）。"""
        w = self.cache / "mirumeai" / kind / name
        (w / "logs" / "01-basic-checks-checks").mkdir(parents=True)
        (w / "logs" / "01-basic-checks-checks" / "03.log").write_text("ok\n", encoding="utf-8")
        if summary:
            (w / "logs" / "summary.json").write_text(json.dumps({"repository": slug, "head": head}), encoding="utf-8")
        ro = w / "job-01" / "venv" / "lib"
        ro.mkdir(parents=True)
        (ro / "x.py").write_text("x\n", encoding="utf-8")
        ro.chmod(0o555)                 # 書けない dir も消せること
        self.readonly.append(ro)
        return w

    def log(self, name: str, head: str, slug: str = SLUG, work: "Path | None" = None, finished: bool = True,
            companions=(".jobs",), text: "str | None" = None) -> Path:
        """`run_required_verification.py` の出力と同じ形の記録（と案件の包みが書く付属のファイル）。"""
        if text is None:
            lines = [f"===== Required Verification: {slug}",
                     f"head: {head}（基準 origin/main {'a' * 12} を含む: はい）",
                     f"正本の道具 MirumeAI/.github origin/main {'b' * 12}・道具 {'c' * 12}（正本と同じ）",
                     "変更のファイル: 2（merge-base 0123456789ab）", "",
                     "basic-checks.yml の job checks: PASS（3 秒）",
                     "  01 passed   機械チェック（1 秒）"]
            if finished:
                lines += ["", f"結果: PASS（{slug} head {head[:12]}・略）", "記録の行（PR に書く）:",
                          f"GitHub Actions unavailable — canonical verification PASS（{slug} head {head[:12]}・略）"]
                lines += [f"段ごとの記録: {work}/logs"] if work is not None else ["rc=0"]
            text = "\n".join(lines) + "\n"
        p = self.rv / name
        p.write_text(text, encoding="utf-8")
        for s in companions:
            (self.rv / (name + s)).write_text("[rv-one] --jobs 2\n", encoding="utf-8")
        return p

    def pr(self, head: str, number: int, state: str = "merged", now: "str | None" = None, slug: str = SLUG) -> None:
        """head に結び付いた Pull Request（足していく）。 実物の GitHub が返す形（merge 済みか開いた物）だけ。
        merge せずに閉じた Pull Request の head には、 実物は空の答えを返す（`closed_only`）。"""
        assert state in ("merged", "open")
        key = f"repos/{slug}/commits/{head}/pulls"
        self.db["answers"].setdefault(key, []).append(
            {"number": number, "state": "closed" if state == "merged" else "open",
             "merged_at": "2026-01-02T03:04:05Z" if state == "merged" else None,
             "head": {"sha": now or head}, "base": {"repo": {"full_name": slug}}})

    def closed_only(self, head: str, slug: str = SLUG) -> None:
        """merge せずに閉じた Pull Request の head（実物の GitHub の答えは空）。"""
        self.answer(head, [], slug)

    def answer(self, head: str, ans, slug: str = SLUG) -> None:
        self.db["answers"][f"repos/{slug}/commits/{head}/pulls"] = ans

    def run(self, *argv, path: "str | None" = None) -> "tuple[int, str, str]":
        self.db_path.write_text(json.dumps(self.db), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        env = {"PATH": path if path is not None else f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "FAKE_GH_DB": str(self.db_path), "XDG_CACHE_HOME": str(self.cache), "HOME": str(self.base / "home"),
               "LANG": "C.UTF-8"}
        with mock.patch.dict(os.environ, env, clear=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = C.main([str(x) for x in argv], running=lambda: self.running)
        self.db = json.loads(self.db_path.read_text(encoding="utf-8"))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self) -> dict:
        """一時の dir の中の、 gh の記録のほかのすべての道と中身（symlink は先を辿らない）。"""
        got = {}
        for d, dirs, files in os.walk(self.base):
            for n in dirs + files:
                p = Path(d) / n
                if p == self.db_path:
                    continue
                rel = str(p.relative_to(self.base))
                if p.is_symlink():
                    got[rel] = "->" + os.readlink(p)
                elif p.is_file():
                    got[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
                else:
                    got[rel] = "dir"
        return got


class Base(unittest.TestCase):

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.w = World(Path(td.name))

        def cleanup() -> None:
            for p in self.w.readonly:
                if p.exists():
                    p.chmod(0o755)
            td.cleanup()
        self.addCleanup(cleanup)

    def tearDown(self) -> None:
        # gh は読むだけ
        for call in self.w.db["calls"]:
            self.assertEqual(call[:3], ["api", "--method", "GET"], call)
            self.assertRegex(call[3], r"^repos/[^/]+/[^/]+/commits/[0-9a-f]{40}/pulls$")

    def assertGone(self, *paths) -> None:
        for p in paths:
            self.assertFalse(os.path.lexists(p), f"{p} が残っている")

    def assertKept(self, *paths) -> None:
        for p in paths:
            self.assertTrue(os.path.lexists(p), f"{p} が消えた")


class DecideTest(Base):

    def test_view_only_deletes_nothing(self) -> None:
        h = sha(1)
        work = self.w.work("1", h)
        log = self.w.log("a.log", h, work=work)
        self.w.pr(h, 12)
        before = self.w.snapshot()
        rc, out, err = self.w.run(self.w.case)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.w.snapshot(), before)
        self.assertIn("見るだけ", out)
        self.assertIn(f"a.log（付属 .jobs）: head {h[:12]}・Pull Request #12 は merge 済み", out)
        self.assertIn("作業の場所（利用者の cache の mirumeai/w/1。 ", out)
        self.assertIn("--apply で消せます（記録 1・付属のファイル 1・作業の場所 1）", out)
        self.assertKept(log, work)

    def test_merged_logs_and_their_work_are_deleted(self) -> None:
        h1, h2 = sha(1), sha(2)
        w1, w2 = self.w.work("1", h1), self.w.work("ab12", h2, kind="sw")
        l1 = self.w.log("a.log", h1, work=w1, companions=(".jobs", ".outer"))
        l2 = self.w.log("b.log", h2, work=w2)
        self.w.pr(h1, 12)
        self.w.pr(h2, 13)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(l1, Path(f"{l1}.jobs"), Path(f"{l1}.outer"), w1, l2, Path(f"{l2}.jobs"), w2)
        self.assertIn("#13 は merge 済み", out)
        self.assertIn("消しました: 7 個（記録 2・付属のファイル 3・作業の場所 2）", out)
        self.assertKept(self.w.cache / "mirumeai" / "w", self.w.cache / "mirumeai" / "sw", self.w.rv)

    def test_open_pr_current_head_is_kept_and_old_head_goes(self) -> None:
        old, now = sha(1), sha(2)
        w_old, w_now = self.w.work("1", old), self.w.work("2", now)
        l_old = self.w.log("old.log", old, work=w_old)
        l_now = self.w.log("now.log", now, work=w_now)
        self.w.pr(old, 20, state="open", now=now)
        self.w.pr(now, 20, state="open")
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(l_old, w_old)
        self.assertKept(l_now, Path(f"{l_now}.jobs"), w_now)
        self.assertIn(f"Pull Request #20 の今の head は別（{now[:12]}）", out)
        self.assertIn(f"now.log: head {now[:12]}・Pull Request #20 の今の head（開いている）", out)

    def test_closed_only_prs_are_kept(self) -> None:
        # 実物の GitHub は、 merge せずに閉じた Pull Request の head に空の答えを返す
        h1, h2 = sha(1), sha(2)
        l1 = self.w.log("a.log", h1, work=self.w.work("1", h1))
        self.w.closed_only(h1)
        # もし閉じただけの Pull Request が返っても、 消さない
        l2 = self.w.log("b.log", h2, work=self.w.work("2", h2))
        self.w.answer(h2, [{"number": 9, "state": "closed", "merged_at": None, "head": {"sha": h2},
                            "base": {"repo": {"full_name": SLUG}}}])
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(l1, l2, self.w.cache / "mirumeai" / "w" / "1", self.w.cache / "mirumeai" / "w" / "2")
        self.assertIn(f"a.log: head {h1[:12]}・Pull Request が見つからない", out)
        self.assertIn("Pull Request #9 は merge せずに閉じた（閉じただけの記録は消さない）", out)

    def test_unexpected_answers_are_kept(self) -> None:
        base = {"base": {"repo": {"full_name": SLUG}}}
        cases = {
            "state.log": lambda h: [{"number": 3, "state": "merged", "merged_at": "x", "head": {"sha": h}, **base}],
            "number.log": lambda h: [{"number": "3", "state": "closed", "merged_at": "x", "head": {"sha": h}, **base}],
            "bool.log": lambda h: [{"number": True, "state": "closed", "merged_at": "x", "head": {"sha": h}, **base}],
            "upper.log": lambda h: [{"number": 3, "state": "open", "merged_at": None, "head": {"sha": h.upper()},
                                     **base}],
            "nohead.log": lambda h: [{"number": 3, "state": "open", "merged_at": None, "head": None, **base}],
            "nobase.log": lambda h: [{"number": 3, "state": "closed", "merged_at": "x", "head": "x", "base": "x"}],
            "item.log": lambda h: ["x"],
        }
        logs = []
        for i, (name, answer) in enumerate(cases.items()):
            h = sha(30 + i)
            logs.append(self.w.log(name, h, work=self.w.work(str(30 + i), h)))
            self.w.answer(h, answer(h))
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(*logs, *(self.w.cache / "mirumeai" / "w" / str(30 + i) for i in range(len(cases))))
        for name in cases:
            self.assertRegex(out, rf"\n  {name}: head [0-9a-f]{{12}}・gh の答えが読めない")
        self.assertIn("消しました: 0 個", out)

    def test_open_current_head_wins_over_other_merged_prs(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h, work=self.w.work("1", h))
        self.w.pr(h, 5)
        self.w.pr(h, 6, state="open")
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(log, self.w.cache / "mirumeai" / "w" / "1")

    def test_logs_without_a_pr_are_kept(self) -> None:
        cases = {"none.log": [], "missing.log": None, "notfound.log": {"error": "gh: Not Found (HTTP 404)"},
                 "broken.log": {"error": "gh: Something (HTTP 500)"}, "garbage.log": {"x": 1},
                 "other.log": [{"number": 3, "state": "closed", "merged_at": "x", "head": {"sha": sha(9)},
                                "base": {"repo": {"full_name": "ExampleOrg/tool-b"}}}]}
        logs = []
        for i, (name, ans) in enumerate(cases.items()):
            h = sha(10 + i)
            logs.append(self.w.log(name, h, work=self.w.work(str(10 + i), h)))
            if ans is not None:
                self.w.answer(h, ans)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(*logs, *(self.w.cache / "mirumeai" / "w" / str(10 + i) for i in range(len(cases))))
        for word in ("none.log: head", "Pull Request が見つからない", "GitHub にこの head が無い",
                     "Repository かこの head を読めない（HTTP 404）", "gh が誤りを返した（終了コード 1）",
                     "gh の答えが読めない"):
            self.assertIn(word, out)
        self.assertIn("消しました: 0 個", out)

    def test_without_gh_everything_is_kept(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h, work=self.w.work("1", h))
        self.w.pr(h, 12)
        empty = self.w.base / "empty-bin"
        empty.mkdir()
        rc, out, err = self.w.run(self.w.case, "--apply", path=str(empty))
        self.assertEqual(rc, 0, err)
        self.assertKept(log, self.w.cache / "mirumeai" / "w" / "1")
        self.assertIn("gh が使えない（gh が無い）", out)

    def test_unreadable_heads_are_kept_without_asking(self) -> None:
        h = sha(1)
        self.w.pr(h, 12)
        good = self.w.log("good.log", h, finished=False).read_text(encoding="utf-8")
        texts = {
            "nohead.log": "流せません: 何か\n",
            "twice.log": good + good,
            "short.log": f"===== Required Verification: {SLUG}\nhead: {h[:12]}\nrc=0\n",
            "local.log": good.replace(SLUG, "local/tool-a") + "rc=0\n",
            "dots.log": good.replace(SLUG, "../tool-a") + "rc=0\n",
            "twosteps.log": good + "段ごとの記録: /x/logs\n段ごとの記録: /y/logs\n",
        }
        (self.w.rv / "good.log").unlink()
        (self.w.rv / "good.log.jobs").unlink()
        for name, text in texts.items():
            self.w.log(name, h, text=text, companions=())
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(*(self.w.rv / n for n in texts))
        for word in ("頭が読めない", "頭が 2 つ以上ある", "head の行が読めない", "GitHub の Repository の記録でない",
                     "段ごとの記録の行が 2 つ以上ある"):
            self.assertIn(word, out)
        self.assertEqual(self.w.db["calls"], [], "読めない記録で GitHub に問わない")

    def test_logs_without_an_end_line_wait_for_running_checks(self) -> None:
        h = sha(1)
        self.w.pr(h, 12)
        for running, kept in ((True, True), (None, True), (False, False)):
            with self.subTest(running=running):
                log = self.w.log("a.log", h, finished=False)
                self.w.running = running
                rc, out, err = self.w.run(self.w.case, "--apply")
                self.assertEqual(rc, 0, err)
                if kept:
                    self.assertKept(log)
                    self.assertIn("終わりの行が無い", out)
                    self.assertIn("注意: この利用者の Required Verification が流れている", out)
                else:
                    self.assertGone(log)
        # 包みが足す終わりの行（rc=）があれば、 流れていても消せる
        self.w.running = True
        log = self.w.log("b.log", h)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(log)

    def test_output_hides_repository_and_paths(self) -> None:
        """Repository の名前（owner/repo）・gh の誤りの文・案件のフォルダと利用者の cache の道・置き場の外の道は
        出さない。 記録のファイルの名前と `.cache/` の下の名前は出す（包みは Repository の短い名前から記録の名前を
        付けるので、 短い名前は出うる。 道具の説明のとおり）。"""
        h1, h2, h3 = sha(1), sha(2), sha(3)
        short = f"customer-zeta-part-12-{h1[:12]}.log"          # 包みの付け方（Repository の短い名前から）
        self.w.log(short, h1, slug=SECRET_SLUG, work=self.w.work("1", h1, slug=SECRET_SLUG))
        self.w.log("b.log", h2, slug=SECRET_SLUG)
        elsewhere = self.w.base / "zeta-elsewhere" / "rv"
        (elsewhere / "logs").mkdir(parents=True)
        self.w.log("c.log", h3, slug=SECRET_SLUG, work=elsewhere)
        self.w.pr(h1, 12, slug=SECRET_SLUG)
        self.w.pr(h3, 13, slug=SECRET_SLUG)
        self.w.answer(h2, {"error": f"gh: Not Found (HTTP 404) https://api.github.com/repos/{SECRET_SLUG}"},
                      slug=SECRET_SLUG)
        (self.w.case / ".cache" / "zeta-notes").mkdir()
        for argv in ((self.w.case,), (self.w.case, "--apply")):
            with self.subTest(argv=argv[1:]):
                rc, out, err = self.w.run(*argv)
                self.assertEqual(rc, 0, err)
                both = out + err
                for hidden in (SECRET_SLUG, "ExampleOrg", str(self.w.base), str(self.w.case), str(self.w.cache),
                               "zeta-elsewhere", "api.github.com"):
                    self.assertNotIn(hidden, both)
                self.assertIn(f"{short}（付属 .jobs）: head {h1[:12]}・Pull Request #12 は merge 済み", out)
                self.assertIn("作業の場所（利用者の cache の mirumeai/w/1。 ", out)
                self.assertIn("c.log の作業の場所（決まった置き場の外の道（道は出さない。", out)
                self.assertIn(".cache/zeta-notes: 決まりの一覧に無い", out)
                self.assertIn("利用者の cache: XDG_CACHE_HOME（道は出さない。", out)
        rc, _, err = self.w.run(self.w.base / "zeta-missing")
        self.assertEqual(rc, 2)
        self.assertNotIn("zeta-missing", err)

    def test_control_characters_in_names_are_escaped(self) -> None:
        h = sha(1)
        self.w.log("a\nb.log", h, companions=())
        self.w.pr(h, 12)
        rc, out, err = self.w.run(self.w.case)
        self.assertEqual(rc, 0, err)
        self.assertIn("a\\u000ab.log", out)


class WorkTest(Base):

    def test_work_outside_the_places_is_reported_not_deleted(self) -> None:
        h = sha(1)
        elsewhere = self.w.base / "elsewhere" / "rv"
        (elsewhere / "logs").mkdir(parents=True)
        (elsewhere / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}), encoding="utf-8")
        under_cache = self.w.cache / "mirumeai" / "other" / "1"
        (under_cache / "logs").mkdir(parents=True)
        (under_cache / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}),
                                                           encoding="utf-8")
        odd = self.w.cache / "mirumeai" / "w" / "x1"      # w/ の下でも、 数でない名前
        (odd / "logs").mkdir(parents=True)
        (odd / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}), encoding="utf-8")
        dotted = f"{self.w.cache}/mirumeai/w/../other/1"
        logs = [self.w.log("a.log", h, work=elsewhere), self.w.log("b.log", h, work=under_cache),
                self.w.log("c.log", h, work=odd), self.w.log("d.log", h, work=dotted)]
        self.w.pr(h, 12)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(*logs)
        self.assertKept(elsewhere / "logs" / "summary.json", under_cache / "logs" / "summary.json",
                        odd / "logs" / "summary.json")
        self.assertIn("a.log の作業の場所（決まった置き場の外の道（道は出さない。 記録の終わりの行を見る））は消さない"
                      "（決まった置き場（利用者の cache の下の", out)
        self.assertIn("b.log の作業の場所（利用者の cache の mirumeai/other/1）は消さない（決まった置き場", out)
        self.assertIn("c.log の作業の場所（利用者の cache の mirumeai/w/x1）は消さない（決まった置き場", out)
        self.assertIn("d.log の作業の場所（利用者の cache の mirumeai/other/1）は消さない（決まった置き場の外）", out)
        self.assertNotIn(str(self.w.base), out)

    def test_work_of_another_check_is_not_deleted(self) -> None:
        h, other = sha(1), sha(2)
        reused = self.w.work("1", other)                  # 名前が使い回されて、 別の検査が使った
        running = self.w.work("2", h, summary=False)      # 流れている検査（まだ summary.json が無い）
        wrong_repo = self.w.work("3", h, slug="ExampleOrg/tool-b")
        logs = [self.w.log("a.log", h, work=reused), self.w.log("b.log", h, work=running),
                self.w.log("c.log", h, work=wrong_repo)]
        self.w.pr(h, 12)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(*logs)
        self.assertKept(reused / "logs" / "summary.json", running / "logs", wrong_repo / "logs" / "summary.json")
        self.assertIn("別の検査の場所", out)
        self.assertIn("流れている検査が使っている見込み", out)

    def test_work_named_by_a_kept_log_is_not_deleted(self) -> None:
        h, now = sha(1), sha(2)
        shared = self.w.work("1", h)
        gone = self.w.log("a.log", h, work=shared)
        kept = self.w.log("b.log", h, work=shared, text=None)
        kept.write_text(kept.read_text(encoding="utf-8").replace(h, now), encoding="utf-8")
        self.w.pr(h, 12)
        self.w.pr(now, 13, state="open")
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(gone)
        self.assertKept(kept, shared / "logs" / "summary.json")
        self.assertIn("残す記録も名指ししている", out)
        # 頭の読めない記録（段ごとの記録の行が 2 つ）が名指しする場所も守る
        h3 = sha(3)
        guarded = self.w.work("3", h3)
        other = self.w.work("4", sha(4))
        self.w.log("odd.log", h3, text=f"段ごとの記録: {other}/logs\n段ごとの記録: {guarded}/logs\n", companions=())
        gone = self.w.log("c.log", h3, work=guarded)
        self.w.pr(h3, 14)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(gone)
        self.assertKept(guarded / "logs" / "summary.json", self.w.rv / "odd.log")

    def test_two_logs_naming_one_work(self) -> None:
        h = sha(1)
        work = self.w.work("1", h)
        a, b = self.w.log("a.log", h, work=work), self.w.log("b.log", h, work=work)
        self.w.pr(h, 12)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(a, b, work)
        self.assertIn("作業の場所 1）", out)

    def test_work_already_gone(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h, work=self.w.cache / "mirumeai" / "w" / "7")
        self.w.pr(h, 12)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(log)
        self.assertIn("消しました: 2 個（記録 1・付属のファイル 1・作業の場所 0）", out)


class SafetyTest(Base):

    def test_symlinks_are_not_followed(self) -> None:
        h = sha(1)
        self.w.pr(h, 12)
        outside = self.w.base / "outside"
        outside.mkdir()
        # 記録の dir の中の symlink（先は記録の形）
        target = outside / "real.log"
        target.write_text(f"===== Required Verification: {SLUG}\nhead: {h}（x）\nrc=0\n", encoding="utf-8")
        (self.w.rv / "link.log").symlink_to(target)
        (self.w.rv / "real.log.jobs").symlink_to(outside / "jobs")
        # 作業の場所そのものが symlink
        far = outside / "far"
        (far / "logs").mkdir(parents=True)
        (far / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}), encoding="utf-8")
        (self.w.cache / "mirumeai" / "w" / "5").symlink_to(far)
        l5 = self.w.log("five.log", h, work=self.w.cache / "mirumeai" / "w" / "5")
        # 付属のファイルが symlink（先を消さない。 記録は消す）
        companion_target = outside / "companion"
        companion_target.write_text("x\n", encoding="utf-8")
        l6 = self.w.log("six.log", h, companions=())
        (self.w.rv / "six.log.outer").symlink_to(companion_target)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(self.w.rv / "link.log", target, far / "logs" / "summary.json",
                        self.w.cache / "mirumeai" / "w" / "5", self.w.rv / "six.log.outer", companion_target)
        self.assertGone(l5, l6)
        self.assertIn("link.log: symlink", out)
        self.assertIn("real.log.jobs: 付属のファイルに対の記録が無い", out)

    def test_linked_places_are_not_entered(self) -> None:
        h = sha(1)
        self.w.pr(h, 12)
        outside = self.w.base / "outside"
        # w/ の置き場そのものが symlink
        real_w = outside / "w"
        (real_w / "1" / "logs").mkdir(parents=True)
        (real_w / "1" / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}),
                                                            encoding="utf-8")
        (self.w.cache / "mirumeai" / "w").rmdir()
        (self.w.cache / "mirumeai" / "w").symlink_to(real_w)
        log = self.w.log("a.log", h, work=self.w.cache / "mirumeai" / "w" / "1")
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(log)
        self.assertKept(real_w / "1" / "logs" / "summary.json")
        self.assertIn("置き場の w/: symlink", out)
        # rv-logs そのものが symlink なら中を見ない
        real_rv = outside / "rv"
        real_rv.mkdir()
        log = real_rv / "b.log"
        log.write_text(f"===== Required Verification: {SLUG}\nhead: {h}（x）\nrc=0\n", encoding="utf-8")
        os.rename(self.w.rv, self.w.base / "old-rv")
        self.w.rv.symlink_to(real_rv)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(log)
        self.assertIn(".cache/rv-logs が symlink か dir でないので、 中を見ない", out)
        # .cache そのものが symlink なら中を見ない
        real_cache = outside / "cache"
        os.rename(self.w.case / ".cache", real_cache)
        (self.w.case / ".cache").symlink_to(real_cache)
        (real_cache / "rv-logs").unlink()           # 先の中は普通の dir と、 消せる形の記録
        (real_cache / "rv-logs").mkdir()
        log = real_cache / "rv-logs" / "c.log"
        log.write_text(f"===== Required Verification: {SLUG}\nhead: {h}（x）\nrc=0\n", encoding="utf-8")
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(log)
        self.assertIn(".cache/ が symlink か dir でないので、 中を見ない", out)

    def test_things_of_another_user_are_not_deleted(self) -> None:
        h = sha(1)
        work = self.w.work("1", h)
        log = self.w.log("a.log", h, work=work)
        self.w.pr(h, 12)
        real_lstat = C._lstat
        theirs = set()

        def lstat(p):
            st = real_lstat(p)
            if st is not None and str(p) in theirs:
                vals = list(st)
                vals[4] = os.getuid() + 1           # st_uid
                return os.stat_result(vals)
            return st
        theirs.add(str(work))                       # 作業の場所だけが、 ほかの利用者の物
        with mock.patch.object(C, "_lstat", side_effect=lstat):
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(log)
        self.assertKept(work / "logs" / "summary.json")
        self.assertIn("は消さない（この利用者の物でない）", out)
        # 記録そのものがほかの利用者の物なら、 記録も作業の場所も残す
        log = self.w.log("b.log", h, work=work)
        theirs.clear()
        theirs.add(str(log))
        with mock.patch.object(C, "_lstat", side_effect=lstat):
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(log, work / "logs" / "summary.json")
        self.assertIn("b.log: この利用者の物でない", out)
        # 置き場（mirumeai/w）がほかの利用者の物でも、 作業の場所を消さない
        theirs.clear()
        theirs.add(str(self.w.cache / "mirumeai" / "w"))
        log.unlink()
        log = self.w.log("c.log", h, work=work)
        with mock.patch.object(C, "_lstat", side_effect=lstat):
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertKept(work / "logs" / "summary.json")
        self.assertIn("置き場の w/: この利用者の物でない", out)

    def test_only_rv_logs_and_named_work_change(self) -> None:
        """作業ルート・`.venvs`・設定・重み・clone など `.cache/` の外の物と、 置かない物に触れない。"""
        h = sha(1)
        self.w.pr(h, 12)
        for rel in ("case_a_data/run1/img.png", ".venvs/web/pyvenv.cfg", "config/a.json", "weights/w.bin",
                    "wheelhouse/a.whl", "tool-a/.git/HEAD", "tool-a/src/x.py", ".secrets/token",
                    ".cache/drafts/pr-1.md", ".cache/clone/.git/HEAD", ".cache/env/pyvenv.cfg",
                    ".cache/nas/a.txt", ".cache/rv.lock"):
            p = self.w.case / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        # 作業ルートを名指しする記録（summary.json まで合っていても、 置き場の外なので消さない）
        data_logs = self.w.case / "case_a_data" / "logs"
        data_logs.mkdir()
        (data_logs / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}), encoding="utf-8")
        work = self.w.work("1", h)
        l1 = self.w.log("a.log", h, work=work)
        l2 = self.w.log("b.log", h, work=self.w.case / "case_a_data")
        before = self.w.snapshot()
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        after = self.w.snapshot()
        removed = {k for k in before if k not in after}
        prefix_work = str(work.relative_to(self.w.base))
        rv_rel = str(self.w.rv.relative_to(self.w.base))
        self.assertEqual(removed, {k for k in before if k.startswith(prefix_work)}
                         | {f"{rv_rel}/a.log", f"{rv_rel}/a.log.jobs", f"{rv_rel}/b.log", f"{rv_rel}/b.log.jobs"})
        self.assertLessEqual(set(after), set(before))
        self.assertEqual(after, {k: before[k] for k in after})
        self.assertGone(l1, l2)

    def test_delete_error_returns_1_and_keeps_the_log(self) -> None:
        if os.getuid() == 0:
            self.skipTest("root は権限で止まらない")
        h = sha(1)
        work = self.w.work("1", h)
        log = self.w.log("a.log", h, work=work)
        self.w.pr(h, 12)
        wdir = self.w.cache / "mirumeai" / "w"
        wdir.chmod(0o555)               # 置き場から作業の場所を外せない
        self.addCleanup(wdir.chmod, 0o755)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("消せませんでした: 作業の場所", err)
        self.assertKept(log)
        self.assertIn("消しました: 0 個", out)


class RecheckTest(Base):
    """見てから消すまでの間（走査の後）に入れ替わった物は、 消さずに誤りにする（消す直前の照らし直し）。"""

    def run_swapped(self, swap) -> "tuple[int, str, str]":
        real = C.scan_logs

        def scan_then_swap(*a, **kw):
            got = real(*a, **kw)
            swap()
            return got
        with mock.patch.object(C, "scan_logs", side_effect=scan_then_swap):
            return self.w.run(self.w.case, "--apply")

    def lstat_as(self, theirs: dict):
        """`_lstat` を、 決めた道だけ持ち主か file system を変えて答える形に差し替える（root でなくても照らせる）。"""
        real = C._lstat

        def lstat(p):
            st = real(p)
            if st is None or str(p) not in theirs:
                return st
            vals = list(st)
            for i, v in theirs[str(p)].items():
                vals[i] = v(vals[i])
            return os.stat_result(vals)
        return mock.patch.object(C, "_lstat", side_effect=lstat)

    def test_work_replaced_by_another_dir(self) -> None:
        h, h2 = sha(1), sha(2)
        work, work2 = self.w.work("1", h), self.w.work("2", h2)
        log, log2 = self.w.log("a.log", h, work=work), self.w.log("b.log", h2, work=work2)
        self.w.pr(h, 12)
        self.w.pr(h2, 13)
        moved = self.w.base / "moved"
        self.w.readonly.append(moved / "job-01" / "venv" / "lib")

        def swap() -> None:
            fresh = self.w.cache / "mirumeai" / "w" / "1.new"   # 中の記録まで合う、 別の dir
            (fresh / "logs").mkdir(parents=True)
            (fresh / "logs" / "summary.json").write_text(json.dumps({"repository": SLUG, "head": h}),
                                                         encoding="utf-8")
            os.rename(work, moved)
            os.rename(fresh, work)
        rc, out, err = self.run_swapped(swap)
        self.assertEqual(rc, 1, out)
        self.assertIn("見たときと違う物になった", err)
        self.assertKept(log, Path(f"{log}.jobs"), work / "logs" / "summary.json", moved / "logs" / "summary.json")
        self.assertGone(log2, work2)                # ほかの記録は続けて扱う
        self.assertIn("消しました: 3 個（記録 1・付属のファイル 1・作業の場所 1）", out)

    def test_work_replaced_by_a_symlink(self) -> None:
        h = sha(1)
        work = self.w.work("1", h)
        log = self.w.log("a.log", h, work=work)
        self.w.pr(h, 12)
        far = self.w.base / "far"
        self.w.readonly.append(far / "job-01" / "venv" / "lib")

        def swap() -> None:
            os.rename(work, far)
            work.symlink_to(far)
        rc, out, err = self.run_swapped(swap)
        self.assertEqual(rc, 1, out)
        self.assertKept(log, work, far / "logs" / "summary.json", far / "job-01" / "venv" / "lib" / "x.py")
        self.assertIn("消しました: 0 個", out)

    def test_log_replaced_by_another_file(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h)
        self.w.pr(h, 12)

        def swap() -> None:
            fresh = self.w.rv / "a.log.new"         # 先に作る（消した inode を使い回さない）
            fresh.write_text("別の中身\n", encoding="utf-8")
            os.replace(fresh, log)
        rc, out, err = self.run_swapped(swap)
        self.assertEqual(rc, 1, out)
        self.assertIn("a.log が見たときと違う物になった", err)
        self.assertEqual(log.read_text(encoding="utf-8"), "別の中身\n")
        self.assertIn("消しました: 1 個（記録 0・付属のファイル 1・作業の場所 0）", out)

    def test_companion_replaced_by_a_symlink(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h)
        self.w.pr(h, 12)
        jobs = Path(f"{log}.jobs")
        target = self.w.base / "target"
        target.write_text("先\n", encoding="utf-8")

        def swap() -> None:
            jobs.unlink()
            jobs.symlink_to(target)
        rc, out, err = self.run_swapped(swap)
        self.assertEqual(rc, 1, out)
        self.assertTrue(jobs.is_symlink())
        self.assertKept(log, target)
        self.assertIn("消しました: 0 個", out)

    def test_log_whose_owner_changed(self) -> None:
        h = sha(1)
        log = self.w.log("a.log", h, companions=())
        self.w.pr(h, 12)
        theirs = {}
        with self.lstat_as(theirs):
            rc, out, err = self.run_swapped(lambda: theirs.update({str(log): {4: lambda uid: uid + 1}}))
        self.assertEqual(rc, 1, out)
        self.assertKept(log)
        self.assertIn("a.log が見たときと違う物になった", err)

    def test_work_with_dirs_the_user_cannot_enter_is_kept(self) -> None:
        if os.getuid() == 0:
            self.skipTest("root は権限で止まらない")
        for i, mode in enumerate((0o000, 0o300, 0o600)):
            with self.subTest(mode=oct(mode)):
                h, ok = sha(100 + i), sha(200 + i)
                work, ok_work = self.w.work(str(100 + i), h), self.w.work(str(200 + i), ok)
                locked = work / "job-01" / "ws" / "locked"
                (locked / "inner").mkdir(parents=True)
                (locked / "inner" / "f").write_text("x\n", encoding="utf-8")
                locked.chmod(mode)
                self.w.readonly.append(locked)
                log, ok_log = self.w.log(f"a{i}.log", h, work=work), self.w.log(f"b{i}.log", ok, work=ok_work)
                self.w.pr(h, 12)
                self.w.pr(ok, 13)
                rc, out, err = self.w.run(self.w.case)
                self.assertEqual(rc, 0, err)
                self.assertIn(f"job-01 の中の dir に本人が入れない（mode {mode:03o}） ので、 --apply では何も消さずに"
                              "誤りになる", out)
                rc, out, err = self.w.run(self.w.case, "--apply")
                self.assertEqual(rc, 1, out)
                self.assertNotIn("Traceback", err)
                self.assertIn("に本人が入れない", err)
                locked.chmod(0o755)
                self.assertKept(log, work / "logs" / "summary.json", work / "job-01" / "venv" / "lib" / "x.py",
                                locked / "inner" / "f")
                self.assertGone(ok_log, ok_work)    # ほかの記録は続けて扱う

    def test_work_with_another_file_system_or_owner_is_kept(self) -> None:
        for what, change in (("別の file system", {2: lambda dev: dev + 1}),
                             ("この利用者の物でない", {4: lambda uid: uid + 1})):
            with self.subTest(what=what):
                h = sha(len(what))
                work = self.w.work(str(len(what)), h)
                inner = work / "job-01" / "ws"
                (inner / "mnt").mkdir(parents=True)
                (inner / "mnt" / "f").write_text("x\n", encoding="utf-8")
                log = self.w.log(f"{len(what)}.log", h, work=work)
                self.w.pr(h, 12)
                with self.lstat_as({str(inner / "mnt"): change}):
                    rc, out, err = self.w.run(self.w.case, "--apply")
                self.assertEqual(rc, 1, out)
                self.assertIn(f"job-01 の中の dir が{what}", err)
                self.assertKept(log, inner / "mnt" / "f", work / "logs" / "summary.json")

    def test_summary_through_a_symlink_is_not_trusted(self) -> None:
        h1, h2 = sha(1), sha(2)
        w1 = self.w.work("1", h1, summary=False)
        real = self.w.base / "real-summary.json"
        real.write_text(json.dumps({"repository": SLUG, "head": h1}), encoding="utf-8")
        (w1 / "logs" / "summary.json").symlink_to(real)
        w2 = self.w.work("2", h2)                   # logs の dir そのものが symlink
        os.rename(w2 / "logs", self.w.base / "real-logs")
        (w2 / "logs").symlink_to(self.w.base / "real-logs")
        l1, l2 = self.w.log("a.log", h1, work=w1), self.w.log("b.log", h2, work=w2)
        self.w.pr(h1, 12)
        self.w.pr(h2, 13)
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertGone(l1, l2)
        self.assertKept(w1 / "logs", real, w2 / "logs", self.w.base / "real-logs" / "summary.json")
        self.assertIn("a.log の作業の場所（利用者の cache の mirumeai/w/1）は消さない（中の記録", out)
        self.assertIn("b.log の作業の場所（利用者の cache の mirumeai/w/2）は消さない（中の記録", out)

    def test_rmtree_refuses_a_symlink(self) -> None:
        target = self.w.base / "target"
        (target / "d").mkdir(parents=True)
        (target / "d" / "f").write_text("x\n", encoding="utf-8")
        link = self.w.base / "link"
        link.symlink_to(target)
        with self.assertRaises(OSError):
            C._rmtree(link)
        self.assertKept(link, target / "d" / "f")

    def test_only_what_is_gone_is_counted(self) -> None:
        h = sha(1)
        work = self.w.work("1", h)
        log = self.w.log("a.log", h, work=work)
        self.w.pr(h, 12)
        with mock.patch.object(C, "_rmtree", return_value=None):     # 消したと言って何も消さない
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("が消えていない", err)
        self.assertKept(log, work / "logs" / "summary.json")
        self.assertIn("消しました: 0 個", out)

    def test_unexpected_errors_do_not_stop_the_others(self) -> None:
        h1, h2 = sha(1), sha(2)
        w1, w2 = self.w.work("1", h1), self.w.work("2", h2)
        l1, l2 = self.w.log("a.log", h1, work=w1), self.w.log("b.log", h2, work=w2)
        self.w.pr(h1, 12)
        self.w.pr(h2, 13)
        real = C._rmtree

        def flaky(path):
            if Path(path) == w1:
                raise TypeError("思わぬ誤り")
            real(path)
        with mock.patch.object(C, "_rmtree", side_effect=flaky):
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 1, out)
        self.assertIn("を消せない（TypeError）", err)
        self.assertKept(l1, w1 / "logs" / "summary.json")
        self.assertGone(l2, w2)


class NoticeTest(Base):

    def test_unknown_and_forbidden_things_are_reported_only(self) -> None:
        files = {".cache/drafts/pr-1.md": "x" * 5000, ".cache/drafts/pr-2.md": "y",
                 ".cache/clone/.git/HEAD": "ref", ".cache/clone/a.py": "a",
                 ".cache/make_venvs/v1/pyvenv.cfg": "home = /usr", ".cache/nas/a.txt": "n",
                 ".cache/screen-walk/a.png": "p", ".cache/rv.lock": "", ".cache/body.md": "b"}
        for rel, text in files.items():
            p = self.w.case / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        (self.w.case / ".cache" / "wt").mkdir()
        (self.w.case / ".cache" / "wt" / ".git").write_text("gitdir: /x\n", encoding="utf-8")   # 作業ツリー
        before = self.w.snapshot()
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.w.snapshot(), before)
        self.assertRegex(out, r"\.cache/drafts: 決まりの一覧に無い（[0-9.]+ (?:B|KiB)・ファイル 2）")
        self.assertIn(".cache/body.md: 決まりの一覧に無い（", out)
        self.assertIn(".cache/clone: .cache/ に置かない物（git の clone・作業ツリー .cache/clone/）", out)
        self.assertIn(".cache/wt: .cache/ に置かない物（git の clone・作業ツリー .cache/wt/）", out)
        self.assertIn(".cache/make_venvs: .cache/ に置かない物（仮想環境 .cache/make_venvs/v1/）", out)
        for known in (".cache/nas", ".cache/screen-walk", ".cache/rv.lock", ".cache/rv-logs"):
            self.assertNotIn(f"{known}:", out)
        rc, out, err = self.w.run(self.w.case, "--keep", "drafts", "--keep", "body.md")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(".cache/drafts:", out)
        self.assertNotIn(".cache/body.md:", out)
        self.assertIn(".cache/clone: .cache/ に置かない物", out)

    def test_other_files_in_rv_logs_are_kept(self) -> None:
        (self.w.rv / "notes.txt").write_text("x\n", encoding="utf-8")
        (self.w.rv / "sub").mkdir()
        (self.w.rv / "lost.log.outer").write_text("x\n", encoding="utf-8")
        before = self.w.snapshot()
        rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.w.snapshot(), before)
        self.assertIn("notes.txt: 記録の形でない（消さない）", out)
        self.assertIn("sub: dir（消さない）", out)
        self.assertIn("lost.log.outer: 付属のファイルに対の記録が無い（消さない）", out)
        self.assertIn("消しました: 0 個", out)

    def test_large_shared_cache_is_reported_only(self) -> None:
        shared = self.w.cache / "mirumeai"
        for rel in ("sync-copies/runs/a/state.json", "sw/ab12/x", "misc/big.bin", "small/a"):
            p = shared / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x" * (20000 if "small" not in rel else 1), encoding="utf-8")
        before = self.w.snapshot()
        with mock.patch.object(C, "SHARED_LARGE", 16 * 1024):
            rc, out, err = self.w.run(self.w.case, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.w.snapshot(), before)
        self.assertIn("利用者の cache の mirumeai/sync-copies（", out)
        self.assertIn("sync_copies.py cleanup --source", out)
        self.assertIn("利用者の cache の mirumeai/sw（", out)
        self.assertIn("利用者の cache の mirumeai/misc（", out)
        self.assertNotIn("mirumeai/small", out)
        rc, out, err = self.w.run(self.w.case)
        self.assertNotIn("利用者の cache の mirumeai/", out)    # 既定の大きさより小さい


class ArgsTest(Base):

    def test_bad_arguments(self) -> None:
        self.assertEqual(self.w.run(self.w.base / "no-such")[0], 2)
        f = self.w.base / "file"
        f.write_text("x", encoding="utf-8")
        self.assertEqual(self.w.run(f)[0], 2)
        for name in ("a/b", "..", "", "a\nb"):
            with self.subTest(name=name):
                rc, _, err = self.w.run(self.w.case, "--keep", name)
                self.assertEqual(rc, 2)
                self.assertIn("--keep", err)
        self.assertEqual(self.w.run()[0], 2)
        self.assertEqual(self.w.run(self.w.case, "--no-such")[0], 2)

    def test_case_without_cache(self) -> None:
        bare = self.w.base / "case-b"
        bare.mkdir()
        rc, out, err = self.w.run(bare, "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn(".cache/ がありません", out)
        self.assertIn("消しました: 0 個", out)


if __name__ == "__main__":
    unittest.main()

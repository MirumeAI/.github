#!/usr/bin/env python3
"""変異の確かめの仕組み（`mutation_engine.py`）の試験。

**この file も写し。** 正本は `MirumeAI/.github` の `scripts/ci/test_mutation_engine.py`。 正本の Required
Verification と各 Core の試験の段で流し、 各 Core の変異の job は、 仕組み自身を壊す行（`ENGINE_MUTATIONS`）で
ここの試験が落ちることを確かめる。 Core に固有のもの（変異の表・括弧の確かめの読み方・workflow の変異の job・
秒の表）は、 各 Core の試験（`tests/ci/test_mutation_baseline.py`）が見る（MirumeAI/.github#206）。

守ること（小さな偽のリポジトリで確かめる）:
  ・壊す前から落ちる検査があれば、 壊さずに失敗を返し、 どれが落ちたかを言う。 通る検査なら壊して確かめる
  ・通ってしまった行・壊す対象が見つからない行・壊す先のファイルが無い行は失敗。 通ってしまった行は、 何が
    起きたのか（出力の末尾）を出す。 行ごとに写しを元へ戻す。 検査ごとの一時の作業ルートを渡して消し、
    バイトコードを書かせない。 `script:` の `.sh` は bash で流す
  ・写しは git の一覧から作り、 写しも git にする。 git で読めないときは Core の決めた除き方で写す
  ・表を分けて流すとき（`--part K/N`）は、 その部分の行だけを流し、 分けた部分を合わせると表の行を 1 度ずつ
    流す。 同じ検査の行をまとめ、 秒の表で部分の時間をそろえる。 表が壊れていても、 どの行も 1 度ずつ流す
  ・当てると読めなくなる変異（Python・シェル・JSON・YAML・画面の script と .js の括弧・systemd の単位）は、
    壊して流す前に表の誤りとして止める。 括弧を見ないのは、 行の試験が**すべて**括弧の確かめの試験のとき
    だけ（#206 の 5-a）。 見なかった種類は言う
  ・試験を読み込めずに落ちた行と、 試験の流し手が結果を書けずに止まった行は、 守りが効いたと数えない
    （#206 の 4-a）
  ・秒の表を作る道具は、 変異の道具が出す結果の行（前の PDM の言い方も）を読む
  ・workflow の変異の job の形（部分の漏れ・分け方・job の違い）を照らす
  ・仕組み自身を壊す行（`ENGINE_MUTATIONS`）は、 どれも壊す先を見つけ、 当てても読め、 照らす試験がここにある
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "mutation_engine.py"
_spec = importlib.util.spec_from_file_location("mutation_engine_under_test", ENGINE)
E = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(E)

GUARD = "def ok(x):\n    return x > 0\n"
TEST_GUARD = ("import unittest\nfrom pkg.guard import ok\n\n\n"
              "class T(unittest.TestCase):\n"
              "    def test_negative_is_refused(self):\n"
              "        self.assertFalse(ok(-1))\n")

#: 偽の Core の、 括弧の確かめを持つ画面の試験（字句の確かめと、 括弧の対応そのものを確かめる試験のクラス）
BRACKET_CHECKER = '''import re
import unittest

SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)
_PAIRS = {")": "(", "]": "[", "}": "{"}


def bracket_error(text):
    stack = []
    for ch in text:
        if ch in "([{":
            stack.append(ch)
        elif ch in _PAIRS:
            if not stack or stack.pop() != _PAIRS[ch]:
                return f"'{ch}' に対応する括弧が無い"
    return f"'{stack[-1]}' が閉じていない" if stack else None


class BalancedTest(unittest.TestCase):
    def test_balanced(self):
        self.assertIsNone(bracket_error("f(){ return [1]; }"))


class OtherTest(unittest.TestCase):
    def test_other(self):
        pass
'''

BRACKETS = E.Brackets(where="checks/brackets.py", tests=("checks.brackets.BalancedTest",),
                      js=lambda m, s: m.bracket_error(s), scripts=lambda m: m.SCRIPT_RE)


class _Repo:
    """小さな偽のリポジトリ（git ではないので、 写しは予備の除き方で作る）。"""

    def setUp(self) -> None:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name)
        self.repo = self.tmp / "repo"
        self.write("pkg/__init__.py", "")
        self.write("pkg/guard.py", GUARD)
        self.write("test_guard.py", TEST_GUARD)

    def write(self, rel: str, text: str) -> Path:
        p = self.repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def table(self, rows, **kw):
        return E.Table(rows=tuple(rows), root=self.repo, seconds=self.tmp / "seconds.json", **kw)

    def main(self, check: str = "test_guard", argv: "list[str] | None" = None, rows=None, **kw
             ) -> "tuple[int, str]":
        rows = rows or [("守り: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", check)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = E.main(argv, self.table(rows, **kw))
        return code, out.getvalue()


class BaselineTest(_Repo, unittest.TestCase):
    """壊す前の確かめと、 行の流し方。"""

    def test_a_check_that_fails_before_breaking_stops_the_run(self) -> None:
        self.write("test_needs_missing.py", "import a_module_that_is_not_installed  # noqa: F401\n")
        code, out = self.main("test_needs_missing")
        self.assertEqual(code, 1, out)
        self.assertIn("test_needs_missing: 壊す前から落ちる", out, "どの検査が落ちたかを言っていない")
        self.assertNotIn("は落ちた", out, "壊す前から落ちる検査で、 守りが効いたと数えた")

    def test_a_script_check_that_fails_before_breaking_stops_the_run(self) -> None:
        self.write("tool.py", "import sys\nsys.exit(1)\n")
        code, out = self.main("script:tool.py")
        self.assertEqual(code, 1, out)
        self.assertIn("script:tool.py: 壊す前から落ちる", out)
        self.assertNotIn("は落ちた", out)

    def test_a_check_that_passes_is_still_used_to_break(self) -> None:
        code, out = self.main("test_guard")
        self.assertEqual(code, 0, out)
        self.assertIn("test_guard は落ちた", out)

    def test_a_part_runs_only_its_rows(self) -> None:
        rows = [("守り 1: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", "test_guard"),
                ("守り 2: -1 も通す", "pkg/guard.py", "return x > 0", "return x > -5", "test_guard")]
        code, out = self.main("test_guard", ["--part", "2/2"], rows)
        self.assertEqual(code, 0, out)
        self.assertIn("守り 2", out)
        self.assertNotIn("守り 1", out, "ほかの部分の行も流した")
        self.assertIn("OK: 表を 2 に分けた 2 番目（1 件）の守りについて", out)
        self.assertNotIn("すべての守り", out, "部分だけを流したのに、 すべての守りと言った")
        # 秒の表に無い検査の数を言う（表を作り直す目安）
        self.assertIn("秒の表に無い検査: 1 個", out)

    def test_the_whole_table_says_every_guard(self) -> None:
        code, out = self.main("test_guard")
        self.assertEqual(code, 0, out)
        self.assertIn("OK: すべての守りについて", out)

    def test_a_row_that_passes_fails_and_says_what_happened(self) -> None:
        self.write("test_weak.py", "import unittest\n\n\nclass T(unittest.TestCase):\n"
                                   "    def test_nothing(self):\n        pass\n")
        code, out = self.main("test_weak")
        self.assertEqual(code, 1, out)
        self.assertIn("test_weak は**通ってしまった**", out)
        self.assertIn("test_weak が落ちなかった（この守りを検証していない）", out)
        self.assertRegex(out, r"(?m)^        \| Ran 1 test", "通ってしまった行で、 何が起きたのかを出していない")

    def test_a_row_whose_target_is_gone_is_a_failure(self) -> None:
        rows = [("守り: 書き方が変わった", "pkg/guard.py", "return x >= 0", "return True", "test_guard"),
                ("守り: ファイルが無い", "pkg/gone.py", "a", "b", "test_guard")]
        code, out = self.main(rows=rows)
        self.assertEqual(code, 1, out)
        self.assertIn("守り: 書き方が変わった: 壊す対象が見つからない", out)
        self.assertIn("守り: ファイルが無い: pkg/gone.py が無い", out)

    def test_each_row_starts_from_the_original(self) -> None:
        rows = [("守り 1: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", "test_guard"),
                ("守り 2: -1 も通す", "pkg/guard.py", "return x > 0", "return x > -5", "test_guard")]
        code, out = self.main(rows=rows)
        self.assertEqual(code, 0, out)
        self.assertEqual((self.repo / "pkg" / "guard.py").read_text(encoding="utf-8"), GUARD, "元を書き換えた")

    def test_no_bytecode_is_written(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("PYTHONDONTWRITEBYTECODE", None)
            ok, tail = E.run(self.repo, "test_guard")
        self.assertTrue(ok, tail)
        self.assertEqual(sorted(self.repo.rglob("*.pyc")), [], "検査がバイトコードを書いた")

    def test_each_run_gets_its_own_work_root(self) -> None:
        """検査ごとに、 作業ルートとデータの起点を新しい一時のフォルダに向け、 流し終えたら消す。

        流す人の環境の値を渡さない。 試験のパッケージの入口が使う印も同じ値にする。 `script:` の行にも渡す。"""
        record = self.tmp / "seen.txt"
        self.write("test_env.py",
                   "import os\nimport unittest\nfrom pathlib import Path\n\n\n"
                   "class T(unittest.TestCase):\n"
                   "    def test_record(self):\n"
                   "        w = os.environ.get('MIRUME_WORK_ROOT', '')\n"
                   f"        with open({str(record)!r}, 'a', encoding='utf-8') as f:\n"
                   "            f.write('\\t'.join([w, os.environ.get('MIRUME_DATA_ROOT', ''),\n"
                   "                    os.environ.get('MIRUME_TESTS_WORK_ROOT', ''),\n"
                   "                    str(bool(w) and Path(w).is_dir())]) + '\\n')\n")
        self.write("env.sh", 'd=False; [ -d "$MIRUME_WORK_ROOT" ] && d=True\n'
                             'printf "%s\\t%s\\t%s\\t%s\\n" "$MIRUME_WORK_ROOT" "$MIRUME_DATA_ROOT" '
                             f'"$MIRUME_TESTS_WORK_ROOT" "$d" >> {shlex.quote(str(record))}\n')
        outside = self.tmp / "outside"
        outside.mkdir()
        with mock.patch.dict(os.environ, {"MIRUME_WORK_ROOT": str(outside), "MIRUME_TESTS_WORK_ROOT": str(outside),
                                          "MIRUME_DATA_ROOT": str(outside / "data")}):
            for check in ("test_env", "test_env", "script:env.sh"):
                ok, tail = E.run(self.repo, check)
                self.assertTrue(ok, tail)
        rows = [ln.split("\t") for ln in record.read_text(encoding="utf-8").splitlines()]
        for work, *_ in rows:
            if work and Path(work) != outside:
                self.addCleanup(shutil.rmtree, work, True)     # 消さない形でも、 一時のフォルダを残さない
        self.assertEqual(len(rows), 3, rows)
        for work, data, mark, was_dir in rows:
            with self.subTest(work=work):
                self.assertTrue(work and Path(work).is_absolute(), rows)
                self.assertNotEqual(Path(work), outside, "流す人の環境の作業ルートを渡した")
                self.assertNotIn(self.repo.resolve(), Path(work).resolve().parents, "写しの中を作業ルートにした")
                self.assertEqual(was_dir, "True", "作業ルートのフォルダが無い")
                self.assertEqual(data, str(Path(work) / "data"), "データの起点が作業ルートの data/ でない")
                self.assertEqual(mark, work, "試験のパッケージの入口が使う印を、 作業ルートと同じにしていない")
                self.assertFalse(Path(work).exists(), "流し終えた一時の作業ルートを消していない")
        self.assertEqual(len({w for w, *_ in rows}), 3, "流すたびに新しくしていない（前の変異の記録が残る）")

    def test_a_shell_script_check_runs_with_bash(self) -> None:
        self.write("ok.sh", 'if [[ -n "x" ]]; then exit 0; fi\nexit 1\n')
        self.write("ok.py", "import sys\nsys.exit(0)\n")
        for check in ("script:ok.sh", "script:ok.py"):
            with self.subTest(check=check):
                ok, tail = E.run(self.repo, check)
                self.assertTrue(ok, tail)


class RowSecondsTest(_Repo, unittest.TestCase):
    """行ごとにかかった秒と、 遅い順の上位を出す。 出すだけで、 合否は変えない。"""

    def test_each_row_says_its_seconds_and_the_total(self) -> None:
        rows = [("守り 1: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", "test_guard"),
                ("守り 2: -1 も通す", "pkg/guard.py", "return x > 0", "return x > -5", "test_guard")]
        code, out = self.main("test_guard", None, rows)
        self.assertEqual(code, 0, out)
        self.assertEqual(len(re.findall(r"test_guard は落ちた（\d+\.\d 秒）", out)), 2, out)
        self.assertRegex(out, r"行ごとの時間: 2 件で計 \d+\.\d 秒（壊す前の確かめ \d+\.\d 秒）")
        self.assertIn("時間のかかった行（遅い順に上位 2 件）", out)
        self.assertTrue(out.rstrip().endswith("壊すとテストが落ちます"), "合否の行が最後でなくなった")

    def report(self, timed: list, slowest: "int | None" = None) -> str:
        out = io.StringIO()
        with mock.patch.object(E, "SLOWEST", slowest or E.SLOWEST), contextlib.redirect_stdout(out):
            E.report_seconds(timed, 0.5)
        return out.getvalue()

    def test_the_slowest_rows_come_first_up_to_the_limit(self) -> None:
        out = self.report([(1.0, "守り a", "x"), (5.0, "守り b", "y"), (3.0, "守り c", "z")], slowest=2)
        listed = re.findall(r"^\s+\d+\.\d 秒  (守り \w) → ", out, re.M)
        self.assertEqual(listed, ["守り b", "守り c"], out)
        self.assertIn("行ごとの時間: 3 件で計 9.0 秒", out)

    def test_the_added_lines_are_not_counted_as_tests(self) -> None:
        # 正本の検査の道具（run_required_verification.py）が、 出力から試験と飛ばした試験の件数を数える形。
        # 足した行がそれに当たると、 件数が狂う
        counted = re.compile(r"^(?:Ran \d+ tests? in |(?:OK|FAILED) \(|合格 \d+ / 不合格 \d+$)", re.M)
        out = self.report([(2.0, "OK (守り)", "Ran 3 tests in x")])
        self.assertIsNone(counted.search(out), out)

    def test_nothing_is_said_without_rows(self) -> None:
        self.assertEqual(self.report([]), "")


class CopyTest(_Repo, unittest.TestCase):
    """壊すための写し。"""

    def git(self, *args: str, cwd: Path) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)

    def test_the_copy_is_what_git_has(self) -> None:
        self.write(".gitignore", "data/\n")
        self.write("data/README.md", "管理している\n")
        self.write("data/big.bin", "管理の外\n")
        self.write("new.py", "X = 1\n")
        self.assertEqual(self.git("init", "-q", cwd=self.repo).returncode, 0)
        self.assertEqual(self.git("add", "-f", "data/README.md", cwd=self.repo).returncode, 0)
        dst = self.tmp / "copy"
        E.copy_repo(self.table([]), dst)
        self.assertTrue((dst / "data" / "README.md").is_file(), "管理しているファイルを写していない")
        self.assertTrue((dst / "new.py").is_file(), "まだ追加していない新しいファイルを写していない")
        self.assertFalse((dst / "data" / "big.bin").exists(), "無視の設定に当たるファイルを写した")
        listed = self.git("ls-files", cwd=dst)
        self.assertEqual(listed.returncode, 0, "写しが git になっていない")
        self.assertIn("new.py", listed.stdout.split())

    def test_without_git_the_core_fallback_is_used(self) -> None:
        self.write("results/x.txt", "学習の結果\n")
        self.write("keep.txt", "写す\n")
        dst = self.tmp / "copy"
        E.copy_repo(self.table([], fallback_ignore=(".git", "results")), dst)
        self.assertTrue((dst / "keep.txt").is_file())
        self.assertFalse((dst / "results").exists(), "Core の決めた除き方を使っていない")


class PartTest(unittest.TestCase):
    """表を分けて流す。"""

    def test_the_parts_run_every_row_once(self) -> None:
        rows = tuple((f"守り {i}", "pkg/guard.py", "a", "b", f"t{i % 4}") for i in range(10))
        with tempfile.TemporaryDirectory() as td:
            table = E.Table(rows=rows, root=Path(td), seconds=Path(td) / "none.json")
            for parts in (1, 2, 3, 12):
                with self.subTest(parts=parts):
                    got = [row for k in range(1, parts + 1) for row in E.rows_of(table, k, parts)]
                    self.assertEqual(len(got), len(rows), "分けた部分を合わせた行の数が、 表の行の数と違う")
                    self.assertEqual(sorted(map(repr, got)), sorted(map(repr, rows)),
                                     "分けた部分を合わせると、 表の行を 1 度ずつ流さない")

    def test_unknown_arguments_are_refused(self) -> None:
        for argv in (["--part", "3/2"], ["--part", "0/2"], ["--part", "x"], ["--parts=1/2"], ["--part"],
                     ["--part", "1/2", "--extra"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    E.parse_part(argv)
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(E.parse_part([]), (1, 1))
        self.assertEqual(E.parse_part(["--part=2/3"]), (2, 3))
        self.assertEqual(E.parse_part(["--part", "3/3"]), (3, 3))


class SpreadTest(unittest.TestCase):
    """表を時間でそろえて分ける。 同じ検査の行をまとめ、 重すぎるまとまりは分ける。"""

    @staticmethod
    def rows(spec: "list[tuple[str, int]]") -> tuple:
        return tuple((f"{test} の守り {i}", "pkg/guard.py", "a", "b", test) for test, n in spec for i in range(n))

    def checks_of(self, rows, plan: "list[list[int]]") -> "list[set[str]]":
        return [{rows[i][4] for i in part} for part in plan]

    def assert_every_row_once(self, rows, plan: "list[list[int]]") -> None:
        self.assertEqual(sorted(i for part in plan for i in part), list(range(len(rows))),
                         "分けた部分を合わせると、 表の行を 1 度ずつ流さない")

    @staticmethod
    def load(rows, plan: "list[list[int]]", secs: "dict[str, float]") -> "list[float]":
        """部分ごとの見積もり（行の秒 + 壊す前の確かめで検査を 1 度ずつ流す秒）。"""
        return [sum(secs[rows[i][4]] for i in part) + sum(secs[t] for t in {rows[i][4] for i in part})
                for part in plan]

    def many(self) -> "tuple[tuple, dict[str, float]]":
        secs = {f"t{i}": 0.5 + (i % 5) for i in range(30)}
        return self.rows([(f"t{i}", 2 + (i * 7) % 11) for i in range(30)]), secs

    def test_rows_of_one_check_stay_together(self) -> None:
        rows = self.rows([("a", 3), ("b", 3)])
        plan = E.plan_parts(2, rows, {"a": 1.0, "b": 1.0})
        self.assert_every_row_once(rows, plan)
        self.assertEqual(sorted(map(sorted, self.checks_of(rows, plan))), [["a"], ["b"]],
                         "同じ検査の行を別々の部分に散らした（壊す前の確かめで、 その検査を何度も流す）")

    def test_a_heavy_check_is_split(self) -> None:
        rows = self.rows([("heavy", 9), ("a", 3), ("b", 3)])
        plan = E.plan_parts(3, rows, {"heavy": 10.0, "a": 1.0, "b": 1.0})
        self.assert_every_row_once(rows, plan)
        holders = [part for part in self.checks_of(rows, plan) if "heavy" in part]
        self.assertGreaterEqual(len(holders), 2, "1 つの部分の目安を超えるまとまりを分けない")

    def test_the_parts_take_about_the_same_time(self) -> None:
        rows, secs = self.many()
        plan = E.plan_parts(6, rows, secs)
        self.assert_every_row_once(rows, plan)
        load = self.load(rows, plan, secs)
        self.assertLessEqual(max(load) - min(load), max(secs.values()) * 2,
                             f"部分の見積もりがそろっていない: {load}")
        # 番号の余りで分ける前の形より、 いちばん重い部分が軽い
        modulo = [[i for i in range(len(rows)) if i % 6 == k] for k in range(6)]
        self.assertLess(max(load), max(self.load(rows, modulo, secs)))

    def test_checks_missing_from_the_table_count_as_the_usual_seconds(self) -> None:
        # 表に無い検査（new）は、 表の中央値（1 秒）で数える。 0 秒と数えると、 軽い部分へ寄せる
        rows = self.rows([("a", 4), ("b", 4), ("new", 8)])
        plan = E.plan_parts(2, rows, {"a": 1.0, "b": 1.0, "other": 1.0})
        self.assert_every_row_once(rows, plan)
        self.assertIn({"new"}, self.checks_of(rows, plan), "表に無い検査を、 中央値で数えていない")

    def test_checks_of_zero_seconds_are_still_spread(self) -> None:
        # 0 秒の検査も下限（MIN_SECONDS）で数える。 0 秒と数えると、 どこに置いても軽さが同じで、 全部を
        # 1 つの部分へ寄せる
        rows = self.rows([("a", 3), ("b", 3)])
        plan = E.plan_parts(2, rows, {"a": 0.0, "b": 0.0})
        self.assert_every_row_once(rows, plan)
        self.assertTrue(all(plan), f"0 秒の検査の行を、 1 つの部分へ寄せた: {plan}")

    def test_the_table_is_used_when_no_seconds_are_given(self) -> None:
        rows = self.rows([("a", 3), ("b", 3), ("c", 3)])
        with tempfile.TemporaryDirectory() as td:
            seconds = Path(td) / "seconds.json"
            seconds.write_text('{"a": 10, "b": 1, "c": 1}', encoding="utf-8")
            plan = E.plan_of(E.Table(rows=rows, root=Path(td), seconds=seconds), 2)
        self.assertIn({"a"}, self.checks_of(rows, plan), "秒の表を読んでいない（重い検査を 1 つの部分にしない）")

    def test_a_broken_table_still_runs_every_row_once(self) -> None:
        rows, _secs = self.many()
        with tempfile.TemporaryDirectory() as td:
            seconds = Path(td) / "seconds.json"
            table = E.Table(rows=rows, root=Path(td), seconds=seconds)
            deep = '{"a": ' + "[" * 100000 + "]" * 100000 + "}"
            huge = '{"t0": 1' + "0" * 400 + "}"
            for body in ("{broken", "[1, 2]", "", deep, huge, '{"t0": 1e308, "t1": 1e308}'):
                with self.subTest(body=body[:60]):
                    seconds.write_text(body, encoding="utf-8")
                    self.assertEqual(E.load_seconds(seconds), {})
                    self.assert_every_row_once(rows, E.plan_of(table, 12))
            seconds.write_text('{"a": "x", "b": -1, "c": true, "d": 2, "e": 1e999}', encoding="utf-8")
            self.assertEqual(E.load_seconds(seconds), {"d": 2.0}, "形の違う値を外していない")
            # 上限（MAX_SECONDS）までは使い、 超える値は外す
            seconds.write_text('{"a": 3600, "b": 3600.5}', encoding="utf-8")
            self.assertEqual(E.load_seconds(seconds), {"a": 3600.0})
            self.assertEqual(E.load_seconds(Path(td) / "none.json"), {})

    def test_the_same_plan_every_time_and_rows_keep_the_table_order(self) -> None:
        rows, secs = self.many()
        plan = E.plan_parts(6, rows, secs)
        self.assertEqual(plan, E.plan_parts(6, rows, secs))
        self.assertEqual(plan, E.plan_parts(6, rows, dict(reversed(list(secs.items())))),
                         "秒の表の並びで分け方が変わる")
        for part in plan:
            self.assertEqual(part, sorted(part), "部分の中の行が、 表の並びでない")


class SyntaxTest(_Repo, unittest.TestCase):
    """当てると読めなくなる変異を止める。 読み込めずに落ちた試験を、 守りが効いたと数えない。"""

    def setUp(self) -> None:
        super().setUp()
        self.write("run.sh", "if true; then\n  echo ok\nfi\n")
        self.write("conf.json", '{"a": 1}\n')

    def errors(self, rows, **kw) -> "list[str]":
        return E.syntax_errors(self.table(rows, **kw), self.repo)

    def notes(self, rows, **kw) -> "list[str]":
        return E.syntax_notes(self.table(rows, **kw), self.repo)

    def test_a_mutation_that_breaks_the_syntax_stops_the_run(self) -> None:
        code, out = self.main(rows=[("守り: 文法ごと壊す", "pkg/guard.py", "return x > 0", "return x >", "test_guard")])
        self.assertEqual(code, 1, out)
        self.assertIn("当てると読めなくなる変異があります", out)
        self.assertIn("守り: 文法ごと壊す", out)
        self.assertNotIn("は落ちた", out, "読めなくなる変異で、 守りが効いたと数えた")

    def test_a_mutation_that_reads_is_still_used_to_break(self) -> None:
        code, out = self.main(rows=[("守り: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", "test_guard")])
        self.assertEqual(code, 0, out)
        self.assertIn("test_guard は落ちた", out)

    def test_an_emptied_block_is_caught(self) -> None:
        """with の中の 1 行だけを空にする（PDM の表に実際にあった形）。"""
        self.write("pkg/guard.py", "import threading\nL = threading.Lock()\n\n\n"
                                   "def ok(x):\n    with L:\n        pass\n    return x > 0\n")
        got = self.errors([("守り: with の中を空にする", "pkg/guard.py", "        pass\n", "", "test_guard")])
        self.assertEqual(len(got), 1, got)
        self.assertIn("守り: with の中を空にする", got[0])

    def test_shell_and_json_are_read_too(self) -> None:
        got = self.errors([("シェル: fi を消す", "run.sh", "fi\n", "", "test_guard"),
                           ("JSON: 括弧を消す", "conf.json", "}", "", "test_guard"),
                           ("読める: 値を変える", "conf.json", "1", "2", "test_guard"),
                           ("読める: 守りを外す", "pkg/guard.py", "return x > 0", "return True", "test_guard")])
        self.assertEqual(len(got), 2, got)
        self.assertIn("シェル: fi を消す", got[0])
        self.assertIn("JSON: 括弧を消す", got[1])

    def test_yaml_is_read_too(self) -> None:
        if E._yaml() is None:
            self.skipTest("PyYAML が無い")
        self.write("wf.yml", "a:\n  b: [1, 2]\n")
        got = self.errors([("YAML: 括弧を消す", "wf.yml", "]", "", "test_guard"),
                           ("読める: 値を変える", "wf.yml", "1", "3", "test_guard")])
        self.assertEqual(len(got), 1, got)
        self.assertIn("YAML: 括弧を消す", got[0])

    def test_a_py_with_a_bom_reads(self) -> None:
        self.write("pkg/bom.py", "\ufeffdef ok(x):\n    return x > 0\n")
        got = self.errors([("BOM: 守りを外す", "pkg/bom.py", "return x > 0", "return True", "test_guard"),
                           ("BOM: 文法ごと壊す", "pkg/bom.py", "return x > 0", "return x >", "test_guard")])
        self.assertEqual(len(got), 1, got)
        self.assertIn("BOM: 文法ごと壊す", got[0])

    def test_page_scripts_and_js_are_read_for_brackets(self) -> None:
        self.write("checks/brackets.py", BRACKET_CHECKER)
        self.write("page.html", "<p>{</p>\n<script>\nfunction f(){ return 1; }\n</script>\n")
        self.write("part.js", "function g(){ return [1]; }\n")
        self.write("broken.js", "x = [1;\n")
        rows = [("画面: script の括弧を消す", "page.html", "return 1; }", "return 1;", "test_guard"),
                ("読める: script の外の文字", "page.html", "<p>{</p>", "<p>{{</p>", "test_guard"),
                ("部品: 括弧を消す", "part.js", "[1]", "[1", "test_guard"),
                ("括弧の試験のクラスと組む: わざと崩す", "part.js", "[1]", "[1", "checks.brackets.BalancedTest"),
                ("括弧の試験の中の試験と組む: わざと崩す", "part.js", "[1]", "[1",
                 "checks.brackets.BalancedTest.test_balanced"),
                # 括弧の試験のクラスを含むモジュール・パッケージの名前は、 ほかの試験も流すので外さない（#206 の 5-a）
                ("括弧の試験のモジュールと組む: 崩すと止める", "part.js", "[1]", "[1", "checks.brackets"),
                ("括弧の試験のパッケージと組む: 崩すと止める", "part.js", "[1]", "[1", "checks"),
                ("同じモジュールのほかの試験と組む: 崩すと止める", "part.js", "[1]", "[1", "checks.brackets.OtherTest"),
                # 括弧の試験とほかの試験を並べた行も、 すべてが括弧の試験ではないので外さない（#206 の 5-a）
                ("括弧の試験とほかの試験と組む: 崩すと止める", "part.js", "[1]", "[1",
                 "checks.brackets.BalancedTest test_guard"),
                ("元から括弧が崩れたファイル: 見ない", "broken.js", "x", "y", "test_guard")]
        got = self.errors(rows, brackets=BRACKETS)
        self.assertEqual([g.split(": 当てると")[0] for g in got],
                         ["画面: script の括弧を消す", "部品: 括弧を消す", "括弧の試験のモジュールと組む: 崩すと止める",
                          "括弧の試験のパッケージと組む: 崩すと止める", "同じモジュールのほかの試験と組む: 崩すと止める",
                          "括弧の試験とほかの試験と組む: 崩すと止める"], got)
        notes = self.notes(rows, brackets=BRACKETS)
        self.assertTrue(any("括弧の対応だけを見る" in n for n in notes), notes)
        self.assertTrue(any("だけと組んだ 2 行は、わざと括弧を崩すので括弧を見ない" in n for n in notes), notes)

    def test_without_the_bracket_checker_it_says_so(self) -> None:
        self.write("part.js", "function g(){ return [1]; }\n")
        rows = [("部品: 括弧を消す", "part.js", "[1]", "[1", "test_guard")]
        self.assertEqual(self.errors(rows, brackets=BRACKETS), [])
        notes = self.notes(rows, brackets=BRACKETS)
        self.assertTrue(any("見ない（括弧の確かめ checks/brackets.py が見つからない）" in n for n in notes), notes)
        self.assertEqual(self.errors(rows), [])
        self.assertTrue(any("見ない（括弧の確かめを持たない）" in n for n in self.notes(rows)), self.notes(rows))

    def test_systemd_units_are_read(self) -> None:
        self.write("a.service", "[Unit]\nDescription=x\n\n# 注釈\n[Service]\nExecStart=/bin/true \\\n  --flag\n")
        got = self.errors([("単位: = を消す", "a.service", "Description=x", "Description x", "test_guard"),
                           ("読める: 値を変える", "a.service", "Description=x", "Description=y", "test_guard"),
                           ("読める: 続きの行", "a.service", "  --flag", "  --other", "test_guard")])
        self.assertEqual(len(got), 1, got)
        self.assertIn("単位: = を消す", got[0])

    def test_kinds_without_a_grammar_are_named(self) -> None:
        self.write(".gitignore", "*.pyc\n")
        rows = [("無視: 1 行消す", ".gitignore", "*.pyc\n", "", "test_guard")]
        self.assertIn("決まった文法が無いので見ない: .gitignore（1 行）", self.notes(rows))
        _code, out = self.main(rows=rows)                 # 流すときも言う（黙って見ないことにしない）
        self.assertIn("文法の確かめ: 決まった文法が無いので見ない: .gitignore（1 行）", out)

    def test_a_test_that_cannot_be_loaded_is_not_counted_as_caught(self) -> None:
        for kind, after in (("NameError", "undefined_name\ndef ok(x):"),
                            ("ImportError", "from pkg import no_such_thing\ndef ok(x):")):
            with self.subTest(kind=kind):
                code, out = self.main(rows=[("守り: 読み込みで落ちる", "pkg/guard.py", "def ok(x):", after, "test_guard")])
                self.assertEqual(code, 1, out)
                self.assertIn("test_guard は**試験を読み込めずに落ちた**", out)
                self.assertNotIn("test_guard は落ちた", out, "読み込めずに落ちたのを、 守りが効いたと数えた")

    def test_the_load_check_reads_results_per_test(self) -> None:
        """読み込みの失敗は、 試験の名前ごとの結果で見る。 流し手が結果を書けずに止まったとき（None）も数えない。"""
        lf = E._load_failed
        self.assertTrue(lf(None), "流し手が結果を書けずに止まったのを、 守りが効いたと数えた（#206 の 4-a）")
        self.assertIsNone(E._read_result(self.repo / "no_such_result.json"))
        self.write("broken.json", "{")
        self.assertIsNone(E._read_result(self.repo / "broken.json"))
        self.assertTrue(lf({"load_failed": ["a"], "in_import": [], "failed": []}))
        self.assertTrue(lf({"load_failed": [], "in_import": ["t.T.test_x"], "failed": ["t.T.test_x"]}))
        self.assertFalse(lf({"load_failed": ["a"], "in_import": [], "failed": ["b.T.test_y"]}),
                         "ほかの試験が捕まえたのを、 読み込みの失敗と数えた")
        self.assertFalse(lf({"load_failed": [], "in_import": [], "failed": ["b.T.test_y"]}))

    def test_a_runner_that_stops_without_a_result_is_not_counted_as_caught(self) -> None:
        """試験の流し手が結果を書けずに止まった行は、 守りを確かめたことにしない（#206 の 4-a）。"""
        self.write("test_exit.py", "import os\nimport unittest\nfrom pkg.guard import ok\n\n\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_negative_is_refused(self):\n"
                                   "        if ok(-1):\n"
                                   "            os._exit(1)        # 流し手が結果を書く前に止まる\n")
        code, out = self.main("test_exit")
        self.assertEqual(code, 1, out)
        self.assertIn("test_exit は**試験を読み込めずに落ちた**", out)
        self.assertNotIn("test_exit は落ちた", out)

    def test_an_assert_message_with_a_child_output_is_still_caught(self) -> None:
        """子の unittest の出力を assert の文に入れて落ちる試験は、 守りが効いたと数える。"""
        self.write("test_guard.py", "import unittest\nfrom pkg.guard import ok\n\n\n"
                                    "class T(unittest.TestCase):\n"
                                    "    def test_negative_is_refused(self):\n"
                                    "        self.assertFalse(ok(-1), '\\nERROR: test_x (unittest.loader._FailedTest)\\n')\n")
        code, out = self.main("test_guard")
        self.assertEqual(code, 0, out)
        self.assertIn("test_guard は落ちた", out)

    def test_one_unloadable_name_beside_a_test_that_caught(self) -> None:
        """名前を並べた行で 1 つだけ読み込めなくても、 ほかの試験が捕まえれば、 守りが効いたと数える。"""
        self.write("pkg/guard.py", "def helper():\n    return 1\n\n\ndef ok(x):\n    return x > 0\n")
        self.write("test_helper.py", "import unittest\nfrom pkg.guard import helper\n\n\n"
                                     "class H(unittest.TestCase):\n    def test_helper(self):\n"
                                     "        self.assertEqual(helper(), 1)\n")
        code, out = self.main(rows=[("守り: 負の数も通す（helper も消える）", "pkg/guard.py",
                                     "def helper():\n    return 1\n\n\ndef ok(x):\n    return x > 0\n",
                                     "def ok(x):\n    return True\n", "test_helper test_guard")])
        self.assertEqual(code, 0, out)
        self.assertIn("test_helper test_guard は落ちた", out)

    LATE = ("import importlib\nimport unittest\n\n\n"
            "class L(unittest.TestCase):\n"
            "    def setUp(self):\n        self.guard = importlib.import_module('pkg.guard')\n\n"
            "    def test_negative_is_refused(self):\n        self.assertFalse(self.guard.ok(-1))\n")

    def test_an_import_error_after_the_test_started_is_not_counted_as_caught(self) -> None:
        """setUp で読み込むモジュールの本体の NameError など（試験が始まった後の読み込みの誤り）も、 守りが効いたと
        数えない。 読み込みの誤りでない例外（ValueError など）は、 守りが効いたと数える。"""
        self.write("test_late.py", self.LATE)
        for kind, after in (("NameError", "undefined_name\ndef ok(x):"),
                            ("ImportError", "from pkg import no_such_thing\ndef ok(x):"),
                            # 本体から呼んだ関数の中・クラスの本体の中（最も内側の枠が <module> でない）
                            ("本体から呼んだ関数の中", "def _h():\n    return undefined_name\n\n\nX = _h()\n\n\ndef ok(x):"),
                            ("クラスの本体の中", "class _C:\n    v = undefined_name\n\n\ndef ok(x):")):
            with self.subTest(kind=kind):
                code, out = self.main(rows=[("守り: 読み込みで落ちる", "pkg/guard.py", "def ok(x):", after, "test_late")])
                self.assertEqual(code, 1, out)
                self.assertIn("test_late は**試験を読み込めずに落ちた**", out)
        code, out = self.main(rows=[("守り: 読み込むと止める", "pkg/guard.py", "def ok(x):",
                                     "raise ValueError('guard')\ndef ok(x):", "test_late")])
        self.assertEqual(code, 0, out)
        self.assertIn("test_late は落ちた", out)

    def test_an_import_error_in_a_subtest_is_not_counted_as_caught(self) -> None:
        """subTest の中で読み込むモジュールの本体の誤りも、 守りが効いたと数えない。"""
        self.write("test_sub.py", "import importlib\nimport unittest\n\n\n"
                                  "class S(unittest.TestCase):\n"
                                  "    def test_negative_is_refused(self):\n"
                                  "        with self.subTest(n=-1):\n"
                                  "            self.assertFalse(importlib.import_module('pkg.guard').ok(-1))\n")
        code, out = self.main(rows=[("守り: 読み込みで落ちる", "pkg/guard.py", "def ok(x):",
                                     "undefined_name\ndef ok(x):", "test_sub")])
        self.assertEqual(code, 1, out)
        self.assertIn("test_sub は**試験を読み込めずに落ちた**", out)

    def test_an_unloadable_name_beside_a_late_import_error_is_not_caught(self) -> None:
        """名前を並べた行で、 1 つが読み込めず、 もう 1 つが本体から呼んだ関数の中の誤りで落ちても、 守りが効いたと
        数えない（どちらの試験も守りを確かめていない）。"""
        self.write("test_late.py", self.LATE)
        code, out = self.main(rows=[("守り: 読み込みで落ちる", "pkg/guard.py", "def ok(x):",
                                     "def _h():\n    return undefined_name\n\n\nX = _h()\n\n\ndef ok(x):",
                                     "test_guard test_late")])
        self.assertEqual(code, 1, out)
        self.assertIn("test_guard test_late は**試験を読み込めずに落ちた**", out)

    def test_the_runner_finds_modules_after_a_chdir(self) -> None:
        """流し手は `python -m unittest` と同じく、 作業の場所を絶対の道で読む。"""
        self.write("test_chdir.py", "import importlib\nimport os\nimport tempfile\nimport unittest\n\n\n"
                                    "class C(unittest.TestCase):\n"
                                    "    def test_import_after_chdir(self):\n"
                                    "        here = os.getcwd()\n"
                                    "        with tempfile.TemporaryDirectory() as d:\n"
                                    "            os.chdir(d)\n"
                                    "            try:\n"
                                    "                self.assertTrue(importlib.import_module('pkg.guard').ok(1))\n"
                                    "            finally:\n"
                                    "                os.chdir(here)\n")
        passed, tail = E.run(self.repo, "test_chdir")
        self.assertTrue(passed, tail)

    def test_the_runner_reads_like_python_m_unittest(self) -> None:
        self.write("test_path.py", "import os\nimport sys\nimport unittest\n\n\n"
                                   "class T(unittest.TestCase):\n"
                                   "    def test_first_path_is_the_cwd(self):\n"
                                   "        self.assertEqual(sys.path[0], os.getcwd())\n")
        r = subprocess.run([sys.executable, "-m", "unittest", "test_path"], cwd=str(self.repo),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-800:])          # -m では通る（比べる元）
        ok, tail = E.run(self.repo, "test_path")
        self.assertTrue(ok, tail)

    @unittest.skipIf(sys.version_info < (3, 12), "`python -m unittest` が 5 で終わるのは 3.12 から")
    def test_a_check_that_runs_no_tests_fails(self) -> None:
        self.write("test_empty.py", "import unittest\n\n\nclass T(unittest.TestCase):\n    pass\n")
        ok, tail = E.run(self.repo, "test_empty")
        self.assertFalse(ok, tail)


class SecondsToolTest(_Repo, unittest.TestCase):
    """秒の表を作る道具（`python scripts/ci/mutation_engine.py seconds`）。"""

    def tool(self, *logs: str) -> "tuple[int, dict | None, str]":
        paths = []
        for n, text in enumerate(logs):
            p = self.tmp / f"{n}.log"
            p.write_text(text, encoding="utf-8")
            paths.append(str(p))
        out = self.tmp / "made.json"
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = E.seconds_main(["--out", str(out), *paths])
        table = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
        return code, table, said.getvalue()

    def test_the_tool_reads_the_lines_main_prints(self) -> None:
        rows = [("守り → 1: 負の数も通す", "pkg/guard.py", "return x > 0", "return True", "test_guard"),
                ("守り 2: -1 も通す", "pkg/guard.py", "return x > 0", "return x > -5", "test_guard")]
        code, out = self.main("test_guard", None, rows)
        self.assertEqual(code, 0, out)
        code, table, said = self.tool(out)
        self.assertEqual(code, 0, said)
        self.assertEqual(list(table), ["test_guard"], "変異の道具が出す結果の行を読めない")

    def test_each_check_gets_the_mean_seconds_of_its_rows(self) -> None:
        log = ("  OK  守り a → tests.x は落ちた（1.0 秒）\n"
               "  OK  守り b → tests.x は落ちた（3.0 秒）\n"
               "  NG  守り c → script:tests/y.sh は**通ってしまった**（0.5 秒）\n"
               "壊す前の確かめ: ...\n")
        code, table, said = self.tool(log, "  OK  守り d → tests.x は落ちた（2.0 秒）\n")
        self.assertEqual(code, 0, said)
        self.assertEqual(table, {"tests.x": 2.0, "script:tests/y.sh": 0.5})

    def test_both_words_of_the_load_failure_are_read(self) -> None:
        # 前の PDM の記録は「検査を読み込めずに」、 今と撮像側は「試験を読み込めずに」
        code, table, said = self.tool("  NG  守り a → tests.x は**検査を読み込めずに落ちた**（1.0 秒）\n"
                                      "  NG  守り b → tests.y は**試験を読み込めずに落ちた**（2.0 秒）\n")
        self.assertEqual(code, 0, said)
        self.assertEqual(table, {"tests.x": 1.0, "tests.y": 2.0})

    def test_nothing_is_written_without_result_lines(self) -> None:
        code, table, said = self.tool("Ran 3 tests in 0.1s\n\nOK\n")
        self.assertEqual(code, 2, said)
        self.assertIsNone(table, "結果の行が無いのに、 秒の表を書いた（表が空になる）")

    def test_the_command_line(self) -> None:
        log = self.tmp / "a.log"
        log.write_text("  OK  守り a → tests.x は落ちた（1.0 秒）\n", encoding="utf-8")
        out = self.tmp / "cli.json"
        r = subprocess.run([sys.executable, str(ENGINE), "seconds", "--out", str(out), str(log)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {"tests.x": 1.0})
        for args in ([], ["seconds"], ["other"]):
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, str(ENGINE), *args], capture_output=True, text=True)
                self.assertEqual(r.returncode, 2, r.stderr)


class WorkflowTest(unittest.TestCase):
    """workflow の変異の job の形（各 Core の試験が、 自分の workflow に使う）。"""

    WF = ("name: x\n\njobs:\n"
          "  product:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n\n"
          "  mutation-1:\n    if: github.event_name != 'push'\n    timeout-minutes: 15\n    steps:\n"
          "      - run: python scripts/ci/mutation_check.py --part 1/2\n\n"
          "  mutation-2:\n    if: github.event_name != 'push'\n    timeout-minutes: 15\n    steps:\n"
          "      # 注釈は見ない（python scripts/ci/mutation_check.py と書いても）\n"
          "      - run: python scripts/ci/mutation_check.py --part 2/2\n")

    def test_a_good_workflow_has_no_problems(self) -> None:
        self.assertEqual(E.workflow_parts(self.WF), [(1, 2), (2, 2)])
        self.assertEqual(E.workflow_problems(self.WF), [])

    def test_problems_are_found(self) -> None:
        cases = (
            ("部分（K）が 1〜N まで漏れなく並んでいない", self.WF.replace("--part 2/2", "--part 1/2")),
            ("分け方（N）がそろっていない", self.WF.replace("--part 2/2", "--part 2/3")),
            ("分けた変異の job の中身が、 部分の番号のほかで違う",
             self.WF.replace("--part 2/2\n", "--part 2/2\n    continue-on-error: true\n")),
            ("変異の job に timeout-minutes: が無い", self.WF.replace("    timeout-minutes: 15\n", "")),
            ("workflow が変異の道具を流していない", "name: x\n\njobs:\n  product:\n    steps:\n      - run: echo\n"),
        )
        for want, wf in cases:
            with self.subTest(want=want):
                got = E.workflow_problems(wf)
                self.assertTrue(any(p.startswith(want) for p in got), got)

    def test_unusual_ways_to_run_the_tool_are_not_let_through(self) -> None:
        self.assertEqual(E.workflow_parts("      python3 scripts/ci/mutation_check.py --part=2/2\n"), [(2, 2)])
        for bad in ("      python scripts/ci/mutation_check.py\n",
                    "      python scripts/ci/mutation_check.py --part 1/2 --verbose\n",
                    "      python scripts/ci/mutation_check.py --part one/2\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    E.workflow_parts(bad)
                self.assertEqual(len(E.workflow_problems("jobs:\n" + bad)), 1)


class EngineRowsTest(unittest.TestCase):
    """仕組み自身を壊す行（`ENGINE_MUTATIONS`）。 正本には変異の job が無いので、 行が古びていないことをここで見る
    （各 Core の変異の job が、 行で試験が落ちることを確かめる）。"""

    ROOT = HERE.parents[1]

    def test_the_rows_point_at_this_engine_and_these_tests(self) -> None:
        self.assertEqual((self.ROOT / E.ENGINE_FILE).resolve(), ENGINE)
        self.assertEqual((self.ROOT / Path(*E.ENGINE_TESTS.split("."))).with_suffix(".py").resolve(),
                         Path(__file__).resolve())

    def test_every_row_finds_its_target_in_the_code_and_reads_after_applying(self) -> None:
        src = ENGINE.read_text(encoding="utf-8")
        table_at = src.index("\nENGINE_MUTATIONS = (")
        self.assertGreaterEqual(len(E.ENGINE_MUTATIONS), 60)
        for label, rel, before, after, _check in E.ENGINE_MUTATIONS:
            with self.subTest(label=label):
                self.assertEqual(rel, E.ENGINE_FILE)
                self.assertIn(before, src, "壊す対象が見つからない（仕組みの書き方が変わった？）")
                self.assertLess(src.index(before), table_at, "壊す先が、 仕組みのコードでなく表の中にある")
                self.assertNotEqual(before, after)
        table = E.Table(rows=E.ENGINE_MUTATIONS, root=self.ROOT, seconds=self.ROOT / "none.json")
        self.assertEqual(E.syntax_errors(table, self.ROOT), [], "当てると読めなくなる行がある")

    def test_every_row_names_tests_that_are_here(self) -> None:
        prefix = E.ENGINE_TESTS + "."
        for label, _rel, _before, _after, check in E.ENGINE_MUTATIONS:
            for name in check.split():
                with self.subTest(label=label, name=name):
                    self.assertTrue(name.startswith(prefix), name)
                    cls, meth = name[len(prefix):].split(".")
                    self.assertTrue(callable(getattr(globals()[cls], meth, None)), f"{name} が無い")

    def test_the_rows_are_distinct(self) -> None:
        labels = [r[0] for r in E.ENGINE_MUTATIONS]
        self.assertEqual(len(labels), len(set(labels)), "同じ説明の行がある")
        pairs = [(r[2], r[3]) for r in E.ENGINE_MUTATIONS]
        self.assertEqual(len(pairs), len(set(pairs)), "同じ壊し方の行がある")


if __name__ == "__main__":
    unittest.main()

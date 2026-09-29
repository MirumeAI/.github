#!/usr/bin/env python3
"""`copy_sync_check.py` が、 複製のずれを見つけることを確かめる。

**複製である以上、 また drift する。** 実際に 3 回起きた。

  ・`basic_checks.py` の改善が 1 Repository にしか入らず、 残り 5 件は
    `.env` や拡張子の無いファイルの鍵を検出できなかった
  ・`import_check.py` の修正が 1 Repository にしか入らなかった
  ・`CLAUDE.md` の件数が 5 Repository で実測とずれていた

どれも人が気づけない形だった。 見つける側が壊れたら元に戻るので、
壊れていないことを実行で確かめる。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "copy_sync_check.py"
ROOT = HERE.parents[1]


def _canonical_tree(dst: Path) -> None:
    """正本の見本を作る。"""
    for rel in ("scripts/ci/basic_checks.py", "scripts/ci/import_check.py",
                "scripts/ci/test_guard_main.sh",
                ".claude/hooks/guard-main.sh"):
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes((ROOT / rel).read_bytes())


class ItFindsDriftTest(unittest.TestCase):

    def _run(self, target: Path, canonical: Path) -> tuple:
        env = dict(os.environ, CI_ROOT=str(target),
                   CANONICAL_ROOT=str(canonical))
        r = subprocess.run([sys.executable, str(CHECKER)],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def test_identical_copies_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            _canonical_tree(tgt)
            code, out = self._run(tgt, can)
            self.assertEqual(code, 0, out[-400:])
            self.assertIn("照合: 4 ファイル", out)

    def test_one_changed_byte_fails(self) -> None:
        """**1 文字違えば止まること。** 見逃すと元に戻る。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            _canonical_tree(tgt)
            f = tgt / "scripts/ci/basic_checks.py"
            f.write_bytes(f.read_bytes() + b"\n# drift\n")
            code, out = self._run(tgt, can)
            self.assertEqual(code, 1, out[-400:])
            self.assertIn("scripts/ci/basic_checks.py", out)
            # **直し方を出すこと。** 止めるだけでは現場で直せない。
            self.assertIn("正本から取り直して", out)

    def test_the_hook_is_covered(self) -> None:
        """**hook も対象であること。**

        Plan 制約で branch protection が使えない Repository では、
        これが main を守る唯一の機械的な壁になる。
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            _canonical_tree(tgt)
            f = tgt / ".claude/hooks/guard-main.sh"
            f.write_bytes(f.read_bytes() + b"\n# drift\n")
            code, out = self._run(tgt, can)
            self.assertEqual(code, 1, out[-400:])
            self.assertIn(".claude/hooks/guard-main.sh", out)

    def test_settings_json_is_not_compared(self) -> None:
        """**`.claude/settings.json` は対象外。**

        Repository ごとの差分が意図的であることを実測で確認している
        （顧客 Repository は `.lic` を deny に追加、 docs Repository は
        持たない script の許可を除外）。
        """
        src = CHECKER.read_text(encoding="utf-8")
        head = src[:src.index("ALLOW_FILE")]
        self.assertNotIn('"settings.json"', head)
        self.assertNotIn(".claude/settings.json", head.split('SHARED = (')[1])

    def test_a_missing_file_is_skipped(self) -> None:
        """持っていない Repository では何もしないこと。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            tgt.mkdir()
            code, out = self._run(tgt, can)
            self.assertEqual(code, 0, out[-400:])
            self.assertIn("照合: 0 ファイル", out)

    def test_the_allow_list_needs_a_path(self) -> None:
        """`.ci-copy-allow` に書いたものは対象外になること。"""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            _canonical_tree(tgt)
            f = tgt / "scripts/ci/basic_checks.py"
            f.write_bytes(f.read_bytes() + b"\n# drift\n")
            (tgt / ".ci-copy-allow").write_text(
                "scripts/ci/basic_checks.py  # 理由をここに書く\n",
                encoding="utf-8")
            code, out = self._run(tgt, can)
            self.assertEqual(code, 0, out[-400:])
            self.assertIn("対象外 1", out)

    def test_the_canonical_itself_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            can = Path(td) / "can"
            _canonical_tree(can)
            code, out = self._run(can, can)
            self.assertEqual(code, 0, out[-400:])
            self.assertIn("正本そのもの", out)

    def test_no_canonical_root_is_an_error(self) -> None:
        """正本の場所が分からないときに「合格」と言わないこと。"""
        with tempfile.TemporaryDirectory() as td:
            env = {k: v for k, v in os.environ.items()
                   if k != "CANONICAL_ROOT"}
            env["CI_ROOT"] = td
            r = subprocess.run([sys.executable, str(CHECKER)],
                               capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 2)


class ItIsWiredIntoTheReusableWorkflowTest(unittest.TestCase):
    """**呼ばれていなければ意味が無い。**"""

    def test_the_workflow_calls_it(self) -> None:
        wf = (ROOT / ".github" / "workflows"
              / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        self.assertIn("copy_sync_check.py", wf)
        self.assertIn("CANONICAL_ROOT", wf)


if __name__ == "__main__":
    unittest.main()

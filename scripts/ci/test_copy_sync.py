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

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "copy_sync_check.py"
ROOT = HERE.parents[1]

_spec = importlib.util.spec_from_file_location("copy_sync_check", CHECKER)
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)


def _canonical_tree(dst: Path) -> None:
    """正本の見本を作る。

    **対象の一覧を `SHARED` から取る。** 見本の一覧を別に持っていたため、
    `SHARED` へ足しても見本は増えず、 足した分は照合されないまま
    「合格」と出ていた。 期待件数も直書きしてあり、 足すたびに落ちた。
    """
    for rel in C.SHARED:
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
            self.assertIn(f"照合: {len(C.SHARED)} ファイル", out)

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

    def test_the_leak_check_is_covered(self) -> None:
        """**混入検査も対象であること。**

        これだけ対象外だった。 script の中に「対象外にするファイル」の表を
        持っており、 中身が Repository ごとに違ったためである。 表を
        `.ci-leak-allow` へ出して同一にした（PDM #61 / IA-PDM #106）。
        """
        self.assertIn("scripts/ci/customer_data_check.py", C.SHARED)
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            can, tgt = base / "can", base / "tgt"
            _canonical_tree(can)
            _canonical_tree(tgt)
            f = tgt / "scripts/ci/customer_data_check.py"
            f.write_bytes(f.read_bytes() + b"\n# drift\n")
            code, out = self._run(tgt, can)
            self.assertEqual(code, 1, out[-400:])
            self.assertIn("scripts/ci/customer_data_check.py", out)

    def test_the_issue_command_is_covered(self) -> None:
        """**Issue を作る手順も対象であること。**

        種別の対応（#38）では、 正本を直した後に 5 Repository へ 1 件ずつ
        同期した。 照合しないと、 同期を忘れた Repository だけ種別の無い
        Issue に戻り、 誰も気づけない。
        """
        for rel in (".claude/scripts/issue-form.sh",
                    ".claude/commands/issue.md"):
            with self.subTest(file=rel), tempfile.TemporaryDirectory() as td:
                self.assertIn(rel, C.SHARED)
                base = Path(td)
                can, tgt = base / "can", base / "tgt"
                _canonical_tree(can)
                _canonical_tree(tgt)
                f = tgt / rel
                f.write_bytes(f.read_bytes() + b"\n# drift\n")
                code, out = self._run(tgt, can)
                self.assertEqual(code, 1, out[-400:])
                self.assertIn(rel, out)

    def test_every_shared_command_is_compared(self) -> None:
        """**共通 command と script は、 すべて照合の対象であること。**

        新しい command を足したときに `SHARED` へ入れ忘れると、 その複製は
        照合されないまま drift する（`customer-request.md` は正本に置かれて
        おらず、 照合できない状態だった）。
        """
        for sub in (".claude/commands", ".claude/scripts"):
            for f in sorted((ROOT / sub).iterdir()):
                if f.is_file():
                    with self.subTest(file=f.name):
                        self.assertIn(f"{sub}/{f.name}", C.SHARED,
                                      f"{sub}/{f.name} が照合の対象に無い")

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

"""`pyproject.toml` の決まり: Core の配布物の名前をどの節にも書かない・依存は PyYAML だけ。

Core の名前を書くと、 依存つきで入れたときに pip が公開の index で Core の名前を探す（依存の宣言漏れの検査も、
`pyproject.toml` の引用の名前をどの節でも宣言と数える）。 正本は MirumeAI/development-docs の
`03_customer_development/recipes_and_machines.md` の「製品のパッケージの入れ方」。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from case_package import cores          # noqa: E402

IA, PDM = cores.DIST_IA, cores.DIST_PDM
TEXT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def core_names_in(text: str) -> "set[str]":
    """文字の中の、 名前の形の字の並び（PEP 503 で正規化した物）のうち、 Core の配布物の名前と同じ物。"""
    words = re.findall(r"[A-Za-z0-9](?:[A-Za-z0-9._\-]*[A-Za-z0-9])?", text)
    return {re.sub(r"[-_.]+", "-", w).lower() for w in words} & {IA, PDM}


class PyprojectTest(unittest.TestCase):

    def test_the_core_names(self) -> None:
        self.assertEqual((IA, PDM), ("mirumeai-image-acquisition", "mirumeai-pdm"))

    def test_pyproject_names_no_core_distribution(self) -> None:
        self.assertEqual(core_names_in(TEXT), set())

    def test_the_reader_sees_every_way_of_writing(self) -> None:
        for text in (f'dependencies = ["{PDM}>=0.7"]', f"x = ['{IA}']", '"MirumeAI_PDM[extra]"',
                     '"Mirumeai.Image_Acquisition"', f"# {PDM}", f'x = """\n{IA} @ file:///w\n"""'):
            with self.subTest(text=text):
                self.assertTrue(core_names_in(text), text)
        for text in (f'"{PDM}-extra"', f'"x{IA}"', "PDM", "mirumeai"):
            with self.subTest(text=text):
                self.assertFalse(core_names_in(text), text)

    def test_the_only_dependency_is_pyyaml(self) -> None:
        """依存は PyYAML だけ（ほかの依存を足すのは Architecture Review）。"""
        deps = re.findall(r"(?m)^dependencies = \[(.*)\]$", TEXT)
        self.assertEqual(deps, ['"PyYAML>=6.0,<7"'])
        self.assertNotIn("[project.optional-dependencies]", TEXT)

    def test_the_build_tools_are_pinned(self) -> None:
        requires = re.findall(r"(?m)^requires = \[(.*)\]$", TEXT)
        self.assertEqual(len(requires), 1)
        for req in re.findall(r'"([^"]+)"', requires[0]):
            with self.subTest(req=req):
                self.assertRegex(req, r"^[A-Za-z0-9_.-]+==[0-9][0-9.]*$")


if __name__ == "__main__":
    unittest.main()

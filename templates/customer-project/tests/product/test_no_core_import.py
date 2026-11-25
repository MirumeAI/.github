"""製品のパッケージ（`src/`）が Core のモジュールを読み込まないこと。

決まり（MirumeAI/development-docs の `03_customer_development/customer_specific_development.md` の「Core の入口」）:
Core の内部を直に import しない。 使うのは各 Core の `docs/public_api.md` の公開の面と差し込みの群だけ。 今の骨組みは
Core をまったく使わないので、 次を断る:

  ・Core のモジュールの `import`・`from … import`
  ・名前を後から決めて読み込む仕組み（`importlib`・`runpy`・`pkgutil` の読み込み、 `exec`・`eval`・`compile`・
    `__import__` の呼び出し、 `sys.path`・`sys.modules` に触れること〔別名の `sys`・`from sys import *` も〕）
  ・Core のモジュールの名前を書いた文字（`-m` で起こす形と `モジュール:属性` の形を含む）

公開の面を使うときは、 ここに使ってよい名前の表を足し、 表の外を断る形にする。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

#: Core の一番上のモジュールの名前（今の名前と前の名前）
CORE_TOPS = frozenset({"mirumeai_ia", "web", "control", "pdm_guard", "mirumeai_auth", "mirumeai_pdm", "pdm"})
DYNAMIC_MODULES = frozenset({"importlib", "runpy", "pkgutil"})
DYNAMIC_CALLS = frozenset({"exec", "eval", "compile", "__import__"})
SYS_TABLES = frozenset({"path", "modules"})
#: Core のモジュールの名前を頭に持つ文字（`.`・`:` で続くか、 それだけ）
CORE_NAME = re.compile(r"(?:%s)(?:[.:]|$)" % "|".join(sorted(CORE_TOPS)))


def _docstrings(tree) -> "set[int]":
    """説明の文字（モジュール・クラス・関数の頭の文字）の id。 名前の説明を断らないため。"""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            first = n.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def problems(text: str) -> "list[str]":
    """源の中で断る所（`<中身>:<行>`。 行の順）。"""
    tree = ast.parse(text)
    docs = _docstrings(tree)
    found: "list[tuple[int, str]]" = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                top = a.name.split(".")[0]
                if top in CORE_TOPS or top in DYNAMIC_MODULES:
                    found.append((n.lineno, f"import {a.name}"))
                elif a.name == "sys" and a.asname:
                    found.append((n.lineno, "import sys as"))
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            top = n.module.split(".")[0]
            if top in CORE_TOPS or top in DYNAMIC_MODULES:
                found.append((n.lineno, f"from {n.module}"))
            elif n.module == "sys" and any(a.name in SYS_TABLES or a.name == "*" for a in n.names):
                found.append((n.lineno, "from sys"))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in DYNAMIC_CALLS:
            found.append((n.lineno, f"{n.func.id}()"))
        elif isinstance(n, ast.Attribute) and n.attr in SYS_TABLES and isinstance(n.value, ast.Name) \
                and n.value.id == "sys":
            found.append((n.lineno, f"sys.{n.attr}"))
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs \
                and CORE_NAME.match(n.value.strip()):
            found.append((n.lineno, repr(n.value)))
    return [f"{what}:{line}" for line, what in sorted(found, key=lambda x: x[0])]


class NoCoreImportTest(unittest.TestCase):

    def test_src_reads_no_core_module(self) -> None:
        files = sorted(SRC.rglob("*.py"))
        self.assertTrue(files)
        found = [f"{f.relative_to(ROOT)} {hit}" for f in files for hit in problems(f.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "製品のパッケージが Core のモジュールを読み込む（か、 読み込みうる形を使う）")

    def test_the_table_of_core_names(self) -> None:
        """名前の表を決めた集合と照らす（表から名前を消すと、 上の照らしが黙って緩む）。"""
        self.assertEqual(CORE_TOPS, {"mirumeai_ia", "web", "control", "pdm_guard", "mirumeai_auth", "mirumeai_pdm",
                                     "pdm"})

    def test_the_reader_sees_the_known_ways(self) -> None:
        """断る形ごとに拾える（拾い方を壊すと、 上の照らしが黙って緩む）。 表のすべての名前を見本に入れる。"""
        src = ("'''説明の文字は mirumeai_ia.web を書いてもよい'''\n"
               "import mirumeai_ia.web.main\n"
               "from pdm import runtime\n"
               "import web.main\n"
               "from control.inspection import result\n"
               "import pdm_guard.app\n"
               "import mirumeai_auth\n"
               "from mirumeai_pdm import runtime\n"
               "import importlib\n"
               "from runpy import run_module\n"
               "import sys as s\n"
               "from sys import modules\n"
               "__import__('x')\n"
               "exec('x = 1')\n"
               "import sys\n"
               "sys.path.append('/x')\n"
               "CMD = ['python', '-m', 'mirumeai_ia.launch']\n"
               "APP = 'pdm_guard.app:app'\n"
               "import pdmx\n"
               "NAME = 'webhook'\n"
               "from sys import argv\n")
        got = [h.rsplit(":", 1)[0] for h in problems(src)]
        self.assertEqual(got, ["import mirumeai_ia.web.main", "from pdm", "import web.main", "from control.inspection",
                               "import pdm_guard.app", "import mirumeai_auth", "from mirumeai_pdm", "import importlib",
                               "from runpy", "import sys as", "from sys", "__import__()", "exec()", "sys.path",
                               "'mirumeai_ia.launch'", "'pdm_guard.app:app'"])
        tops = {g.split()[-1].split(".")[0] for g in got if g.startswith(("import ", "from "))}
        self.assertTrue(CORE_TOPS <= tops, CORE_TOPS - tops)


if __name__ == "__main__":
    unittest.main()

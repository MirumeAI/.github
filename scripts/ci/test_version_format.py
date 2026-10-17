#!/usr/bin/env python3
"""`basic_checks.py` の `VERSION` の形を確かめる。

**許すのは Release の版 `X.Y.Z` と、 開発の版 `X.Y.Z.devN`（PEP 440）だけ。**
`main` は Release の後に開発の版を名乗る。 それ以外の形（rc・接頭辞・空白・
複数行・`v` 付き・自由な文字）は、 タグと突き合わせられないので落とす。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "basic_checks.py"


def run(version_body: str) -> "tuple[int, str]":
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        (root / "VERSION").write_text(version_body, encoding="utf-8")
        subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
        env = {**os.environ, "CI_ROOT": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
        r = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr


class VersionFormatTest(unittest.TestCase):

    def test_release_and_dev_versions_pass(self) -> None:
        for body in ("0.3.0\n", "1.20.300\n", "0.3.1.dev0\n", "0.4.0.dev12\n"):
            code, out = run(body)
            self.assertEqual(code, 0, (body, out[-400:]))

    def test_other_forms_fail(self) -> None:
        for body in ("0.3\n", "v0.3.0\n", "0.3.0rc1\n", "0.3.0.dev\n", "0.3.0.dev0.post1\n",
                     "0.3.0-dev0\n", "0.3.0+local\n", "0.3.0.devX\n", "latest\n"):
            code, out = run(body)
            self.assertEqual(code, 1, (body, out[-400:]))
            self.assertIn("VERSION", out)

    def test_the_line_rules_still_hold(self) -> None:
        for body in ("0.3.0", " 0.3.0\n", "0.3.0\n\n", "0.3.0.dev0\nextra\n"):
            code, out = run(body)
            self.assertEqual(code, 1, (repr(body), out[-400:]))

    def test_the_content_is_not_printed(self) -> None:
        code, out = run("zzfakevalue\n")
        self.assertEqual(code, 1)
        self.assertNotIn("zzfakevalue", out)


class ItIsWiredIntoCiTest(unittest.TestCase):

    def test_the_workflow_runs_this_test(self) -> None:
        wf = (HERE.parents[1] / ".github" / "workflows" / "basic-checks.yml").read_text(encoding="utf-8")
        self.assertIn("test_version_format.py", wf)


if __name__ == "__main__":
    unittest.main()

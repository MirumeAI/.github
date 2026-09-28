#!/usr/bin/env python3
"""秘密情報の走査が、 **拡張子で取りこぼさない**ことを確かめる。

実測で分かったこと: `basic_checks.py` は 6 Repository に複製されていて、
**改善版は 1 件にしか入っていなかった**。 残り 5 件は

    .env に書いた AWS キー            -> 検出しない
    拡張子の無いファイルの GitHub token -> 検出しない

という状態だった。 拡張子で絞ると、 拡張子の無いファイルが丸ごと
素通りする（`configs/active_profile` のような実在のファイルが該当）。
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

#: 検出されるべき見本。 **本物ではない**（公式文書の例示値と同じ形）。
SAMPLES = {
    ".env": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n",
    "configs/active_profile": "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n",
    "deploy/site.conf": "aws = AKIAIOSFODNN7EXAMPLE\n",
}


class SecretScanCoversEveryTextFileTest(unittest.TestCase):

    def _run(self, files: dict) -> str:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
            for rel, body in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
            subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
            env = dict(os.environ, CI_ROOT=str(root))
            r = subprocess.run([sys.executable, str(CHECKER)],
                               capture_output=True, text=True, env=env)
            return r.stdout + r.stderr

    def test_each_kind_is_found(self) -> None:
        for rel, body in SAMPLES.items():
            with self.subTest(file=rel):
                out = self._run({rel: body, "README.md": "ok\n"})
                self.assertIn("NG", out, f"{rel} を見逃しています")
                self.assertIn(rel, out)

    def test_a_file_without_a_suffix_is_scanned(self) -> None:
        """**拡張子で絞らないこと。** 実在のファイルが素通りしていた。"""
        out = self._run({"configs/active_profile":
                         "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"})
        self.assertIn("configs/active_profile", out)

    def test_a_clean_repository_passes(self) -> None:
        out = self._run({"README.md": "ok\n", ".env": "DEBUG=1\n"})
        self.assertIn("OK", out)
        self.assertNotIn("NG", out)

    def test_a_binary_file_is_not_scanned(self) -> None:
        """拡張子が無くても中身がバイナリなら見ない（誤検出と時間の無駄）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
            (root / "blob").write_bytes(b"\x00\x01\x02" + b"AKIAIOSFODNN7EXAMPLE")
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
            env = dict(os.environ, CI_ROOT=str(root))
            r = subprocess.run([sys.executable, str(CHECKER)],
                               capture_output=True, text=True, env=env)
            self.assertNotIn("NG", r.stdout + r.stderr)


class TheValidatorsFollowCiRootTest(unittest.TestCase):
    """**3 つとも `CI_ROOT` を見ること。**

    再利用 workflow では validator が `.github` 側に置かれ、 検査したいのは
    呼び出し側 Repository になる。 1 つでも自分の場所を見ると、 平台自身を
    検査して「合格」と言ってしまう。
    """

    def test_every_validator_reads_ci_root(self) -> None:
        for name in ("basic_checks.py", "import_check.py",
                     "test_guard_main.sh"):
            with self.subTest(validator=name):
                src = (HERE / name).read_text(encoding="utf-8")
                self.assertIn("CI_ROOT", src, f"{name} が CI_ROOT を見ていない")

    def test_the_hook_test_checks_the_caller(self) -> None:
        """`CI_ROOT` を渡したとき、 そちらの hook を検証すること。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hook_dir = root / ".claude" / "hooks"
            hook_dir.mkdir(parents=True)
            src = (HERE.parents[1] / ".claude" / "hooks"
                   / "guard-main.sh").read_text(encoding="utf-8")
            (hook_dir / "guard-main.sh").write_text(src, encoding="utf-8")
            env = dict(os.environ, CI_ROOT=str(root))
            r = subprocess.run(["bash", str(HERE / "test_guard_main.sh")],
                               capture_output=True, text=True, env=env)
            self.assertIn(str(root), r.stdout,
                          "呼び出し側の hook を検証していない")
            self.assertEqual(r.returncode, 0, r.stdout[-400:])


if __name__ == "__main__":
    unittest.main()

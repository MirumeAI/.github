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

#: 見本の値は **実行時に組み立てる**。 本物ではないが、 そのまま書くと
#: `basic_checks.py` が**このファイル自身**を検出して CI が落ちる
#: （実際に落ちた）。 ファイルごと `.ci-ignore` で除外すると、 後から
#: 本物が混ざっても気づけなくなるので、 除外はしない。
_AWS = "AKIA" + "IOSFODNN7EXAMPLE"
_GH = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"

#: 検出されるべき見本。
SAMPLES = {
    ".env": f"AWS_ACCESS_KEY_ID={_AWS}\n",
    "configs/active_profile": f"token: {_GH}\n",
    "deploy/site.conf": f"aws = {_AWS}\n",
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
        out = self._run({"configs/active_profile": f"token: {_GH}\n"})
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
            (root / "blob").write_bytes(b"\x00\x01\x02" + _AWS.encode())
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


class NewFilesAreCheckedBeforeAddTest(unittest.TestCase):
    """**`git add` する前の新しいファイルも検査すること。**

    `git ls-files` だけを見ていたので、 手元で「OK」と出たものが
    commit した後の CI で初めて落ちた。 **今日 3 回起きた**
    （org_audit の追加 / validator の集約 / 見本の秘密情報）。

    `.gitignore` に入っているものは今までどおり対象外にする。 生成物や
    秘密の置き場を検査に入れると、 毎回 NG になって誰も見なくなる。
    """

    def _run(self, checker: str, files: dict, ignore: str = "") -> str:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
            if ignore:
                (root / ".gitignore").write_text(ignore, encoding="utf-8")
            (root / "README.md").write_text("ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            # **ここから先は add しない。** それが本題。
            for rel, body in files.items():
                p_ = root / rel
                p_.parent.mkdir(parents=True, exist_ok=True)
                p_.write_text(body, encoding="utf-8")
            env = dict(os.environ, CI_ROOT=str(root))
            r = subprocess.run([sys.executable, str(HERE / checker)],
                               capture_output=True, text=True, env=env)
            return r.stdout + r.stderr

    def test_a_new_file_is_checked_by_basic_checks(self) -> None:
        out = self._run("basic_checks.py", {"zz_new.py": "def broken(:\n"})
        self.assertIn("zz_new.py", out,
                      "add 前の新しいファイルを検査していない")
        self.assertIn("NG", out)

    def test_a_new_file_is_checked_by_import_check(self) -> None:
        out = self._run("import_check.py",
                        {"zz_new.py": "import totally_absent_pkg\n"})
        self.assertIn("zz_new.py", out,
                      "add 前の新しいファイルを検査していない")

    def test_an_ignored_file_is_left_alone(self) -> None:
        """**追跡しないと決めたものは対象外のまま。**"""
        out = self._run("basic_checks.py",
                        {"build/zz_new.py": "def broken(:\n"},
                        ignore="build/\n")
        self.assertNotIn("build/zz_new.py", out)
        self.assertIn("OK", out)

    def test_a_clean_new_file_passes(self) -> None:
        out = self._run("basic_checks.py", {"zz_new.py": "x = 1\n"})
        self.assertIn("OK", out)
        self.assertNotIn("NG", out)


class TheDocumentedCountMustMatchTest(unittest.TestCase):
    """**数字を作る側が、 文書との一致も確かめること。**

    5 Repository の `CLAUDE.md` が「63 ケース」と書いていたが、 実行すると
    65 ケース通る状態だった。 ケースを足したときに数字を直す仕組みが無く、
    **文章に書いた数字は実装が増えても変わらない**。 人では気づけない。
    """

    def _run(self, claude_md: "str | None") -> tuple:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            hooks = root / ".claude" / "hooks"
            hooks.mkdir(parents=True)
            src = (HERE.parents[1] / ".claude" / "hooks"
                   / "guard-main.sh").read_text(encoding="utf-8")
            (hooks / "guard-main.sh").write_text(src, encoding="utf-8")
            if claude_md is not None:
                (root / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
            env = dict(os.environ, CI_ROOT=str(root))
            r = subprocess.run(["bash", str(HERE / "test_guard_main.sh")],
                               capture_output=True, text=True, env=env)
            return r.returncode, r.stdout + r.stderr

    def test_a_stale_count_fails(self) -> None:
        code, out = self._run("`test_guard_main.sh` が 63 ケースで検証している。")
        self.assertEqual(code, 1, "ずれているのに通っています")
        self.assertIn("不一致", out)
        self.assertIn("63", out)

    def test_a_matching_count_passes(self) -> None:
        # 実測の件数をまず取る（ケースを足しても壊れないように）
        _, out = self._run(None)
        import re
        m = re.search(r"合格 (\d+)", out)
        self.assertIsNotNone(m)
        code, out2 = self._run(
            f"`test_guard_main.sh` が {m.group(1)} ケースで検証している。")
        self.assertEqual(code, 0, out2[-300:])
        self.assertNotIn("不一致", out2)

    def test_no_count_is_not_an_error(self) -> None:
        """件数を書いていない Repository では何もしないこと。"""
        code, out = self._run("件数は書かない。")
        self.assertEqual(code, 0, out[-300:])
        self.assertNotIn("不一致", out)

    def test_no_claude_md_is_not_an_error(self) -> None:
        code, out = self._run(None)
        self.assertEqual(code, 0, out[-300:])


if __name__ == "__main__":
    unittest.main()

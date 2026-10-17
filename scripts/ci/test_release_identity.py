#!/usr/bin/env python3
"""`release_identity_check.py` が、 Product Release の識別の崩れを見つけることを確かめる。

**版の文字・注釈つきのタグ・commit が 1 対 1 に対応しないと、 同じ版を名乗る別のコードが
生まれる。** 実際に、 main に無いタグが main と同じ版を名乗っていた。 ここでは一時の
Repository に、 次の形を作って確かめる（どれも捨てる Repository の中だけのタグ）。

    軽量のタグ・main に無いタグ・版の出どころとの食い違い・開発の版・形の違うタグ
    前の Product Release に LEGACY を使わない・記録したタグと今のタグの食い違い（動いた）
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "release_identity_check.py"
sys.path.insert(0, str(HERE))
import release_identity_check as ric  # noqa: E402

FAKE_ID = "zzfakecaseidentifier"  # 案件の識別子の代わり（実在しない語）


class Repo:
    """一時の Repository。 設定は手元の設定から切り離す。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = {**os.environ, "HOME": str(root.parent), "GIT_CONFIG_NOSYSTEM": "1",
                    "GIT_CONFIG_GLOBAL": os.devnull,
                    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
        root.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(self.root), *args], capture_output=True,
                           text=True, env=self.env, check=True)
        return r.stdout.strip()

    def commit(self, msg: str, files: dict) -> str:
        for rel, body in files.items():
            (self.root / rel).write_text(body, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str, annotated: bool = True, at: str = "HEAD") -> None:
        if annotated:
            self.git("tag", "-a", name, at, "-m", "release")
        else:
            self.git("tag", name, at)


def pyproject(v: str) -> str:
    return f'[build-system]\nrequires = ["setuptools"]\n\n[project]\nname = "x"\nversion = "{v}"\n'


def run(*args: str) -> "tuple[int, str]":
    r = subprocess.run([sys.executable, str(CHECKER), *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


class PyprojectTest(unittest.TestCase):
    """PDM の形（版の出どころは pyproject.toml）。 LEGACY のタグも置く。"""

    def setUp(self) -> None:
        self.td = tempfile.TemporaryDirectory()
        self.r = Repo(Path(self.td.name) / "repo")
        r = self.r
        self.c1 = r.commit("first", {"pyproject.toml": pyproject("0.2.0")})
        r.tag("v0.2.0", annotated=False)                       # LEGACY: 軽量のタグ
        r.git("switch", "-q", "-c", "side")
        r.commit("side", {"pyproject.toml": pyproject("0.3.0")})
        r.tag("v0.3.0")                                        # LEGACY: main に無いタグ
        r.tag(f"{FAKE_ID}/v1.0.0")                              # LEGACY: 案件の接頭辞つき
        r.git("switch", "-q", "main")
        self.c2 = r.commit("subject-that-must-not-appear", {"pyproject.toml": pyproject("0.4.0")})
        r.tag("v0.4.0")                                        # 新しい形の最初
        self.obj2 = r.git("rev-parse", "refs/tags/v0.4.0")

    def tearDown(self) -> None:
        self.td.cleanup()

    def check(self, tag: str, *extra: str) -> "tuple[int, str]":
        return run("check", "--repo-root", str(self.r.root), "--tag", tag,
                   "--version-source", "pyproject", "--first-version", "0.4.0",
                   "--main-ref", "main", *extra)

    def release_json(self, rows: list) -> str:
        p = Path(self.td.name) / "releases.json"
        p.write_text(json.dumps(rows), encoding="utf-8")
        return str(p)

    def body(self, obj: str, commit: str) -> str:
        return f"## Product Release\n\n- tag object: {obj}\n- commit: {commit}\n"

    def test_first_new_style_release_passes_and_ignores_legacy(self) -> None:
        code, out = self.check("v0.4.0")
        self.assertEqual(code, 0, out)
        self.assertIn(f"commit={self.c2}", out)
        self.assertIn(f"tag_object={self.obj2}", out)
        self.assertIn("previous_tag=\n", out)                  # LEGACY は前の Release にしない

    def test_lightweight_tag_fails(self) -> None:
        self.r.commit("next", {"pyproject.toml": pyproject("0.4.1")})
        self.r.tag("v0.4.1", annotated=False)
        code, out = self.check("v0.4.1")
        self.assertEqual(code, 1, out)
        self.assertIn("注釈つきのタグではない", out)

    def test_tag_not_on_main_fails(self) -> None:
        self.r.git("switch", "-q", "-c", "topic")
        self.r.commit("topic", {"pyproject.toml": pyproject("0.4.1")})
        self.r.tag("v0.4.1")
        self.r.git("switch", "-q", "main")
        code, out = self.check("v0.4.1")
        self.assertEqual(code, 1, out)
        self.assertIn("main の祖先ではない", out)

    def test_version_mismatch_fails(self) -> None:
        self.r.commit("next", {"README": "x"})               # 版は 0.4.0 のまま
        self.r.tag("v0.4.1")
        code, out = self.check("v0.4.1")
        self.assertEqual(code, 1, out)
        self.assertIn("違う", out)

    def test_dev_version_fails(self) -> None:
        self.r.commit("dev", {"pyproject.toml": pyproject("0.4.1.dev0")})
        self.r.tag("v0.4.1")
        code, out = self.check("v0.4.1")
        self.assertEqual(code, 1, out)
        self.assertIn("開発の版", out)

    def test_other_tag_forms_fail_without_echoing_the_name(self) -> None:
        for bad in (f"{FAKE_ID}/v1.0.0", "v0.4.1rc1", "v0.4", "0.4.0", "v0.4.0.dev0"):
            code, out = self.check(bad)
            self.assertEqual(code, 1, (bad, out))
            self.assertNotIn(FAKE_ID, out)

    def test_versions_before_first_version_fail(self) -> None:
        code, out = self.check("v0.3.0")
        self.assertEqual(code, 1, out)
        self.assertIn("LEGACY", out)

    def test_previous_is_the_nearest_new_style_release(self) -> None:
        c3 = self.r.commit("next", {"pyproject.toml": pyproject("0.4.1")})
        self.r.tag("v0.4.1")
        code, out = self.check("v0.4.1")
        self.assertEqual(code, 0, out)
        self.assertIn(f"commit={c3}", out)
        self.assertIn("previous_tag=v0.4.0", out)

    def test_previous_needs_a_github_release_when_the_list_is_given(self) -> None:
        self.r.commit("next", {"pyproject.toml": pyproject("0.4.1")})
        self.r.tag("v0.4.1")
        code, out = self.check("v0.4.1", "--releases-json", self.release_json([]))
        self.assertEqual(code, 0, out)
        self.assertIn("previous_tag=\n", out)
        rows = [{"tag_name": "v0.4.0", "body": self.body(self.obj2, self.c2)}]
        code, out = self.check("v0.4.1", "--releases-json", self.release_json(rows))
        self.assertEqual(code, 0, out)
        self.assertIn("previous_tag=v0.4.0", out)
        self.assertIn("drift_checked=1", out)

    def test_drift_finds_a_moved_tag(self) -> None:
        rows = [{"tag_name": "v0.4.0", "body": self.body(self.obj2, self.c1)}]   # 記録と違う commit
        code, out = run("drift", "--repo-root", str(self.r.root), "--first-version", "0.4.0",
                        "--releases-json", self.release_json(rows))
        self.assertEqual(code, 1, out)
        self.assertIn("動いた", out)

    def test_drift_finds_a_missing_record_and_a_deleted_tag(self) -> None:
        rows = [{"tag_name": "v0.4.0", "body": "commit subjects only"}]
        code, out = run("drift", "--repo-root", str(self.r.root), "--first-version", "0.4.0",
                        "--releases-json", self.release_json(rows))
        self.assertEqual(code, 1, out)
        rows = [{"tag_name": "v0.4.5", "body": self.body(self.obj2, self.c2)}]
        code, out = run("drift", "--repo-root", str(self.r.root), "--first-version", "0.4.0",
                        "--releases-json", self.release_json(rows))
        self.assertEqual(code, 1, out)
        self.assertIn("タグが無い", out)

    def test_drift_ignores_legacy_releases(self) -> None:
        rows = [{"tag_name": "v0.3.0", "body": "old notes"}, {"tag_name": "v0.2.0", "body": ""},
                {"tag_name": "v0.4.0", "body": self.body(self.obj2, self.c2)}]
        code, out = run("drift", "--repo-root", str(self.r.root), "--first-version", "0.4.0",
                        "--releases-json", self.release_json(rows))
        self.assertEqual(code, 0, out)
        self.assertIn("1 件", out)

    def test_notes_carry_identity_only(self) -> None:
        notes = Path(self.td.name) / "notes.md"
        code, out = self.check("v0.4.0", "--product", "PDM", "--ci-run-url", "https://example.invalid/run/1",
                               "--notes-out", str(notes))
        self.assertEqual(code, 0, out)
        text = notes.read_text(encoding="utf-8")
        for key in ("- product: PDM", "- version: 0.4.0", "- tag: v0.4.0", f"- tag object: {self.obj2}",
                    f"- commit: {self.c2}", "- CI run: https://example.invalid/run/1", "- artifact:",
                    "- production:"):
            self.assertIn(key, text)
        self.assertNotIn("subject-that-must-not-appear", text)  # commit の件名を載せない
        self.assertNotIn(FAKE_ID, text)
        # 次の Release の drift が、 このノートの記録を読めること
        rows = [{"tag_name": "v0.4.0", "body": text}]
        code, out = run("drift", "--repo-root", str(self.r.root), "--first-version", "0.4.0",
                        "--releases-json", self.release_json(rows))
        self.assertEqual(code, 0, out)

    def test_preflight(self) -> None:
        c3 = self.r.commit("next", {"pyproject.toml": pyproject("0.4.1")})
        base = ("preflight", "--repo-root", str(self.r.root), "--version-source", "pyproject",
                "--first-version", "0.4.0", "--main-ref", "main")
        code, out = run(*base, "--tag", "v0.4.1", "--commit", c3)
        self.assertEqual(code, 0, out)
        self.assertIn("Core の maintainer（人）が打つ", out)
        code, out = run(*base, "--tag", "v0.4.0", "--commit", self.c2)
        self.assertEqual(code, 1, out)
        self.assertIn("もうある", out)
        code, out = run(*base, "--tag", "v0.4.2", "--commit", c3)
        self.assertEqual(code, 1, out)                         # 版が違う


class VersionFileTest(unittest.TestCase):
    """Image-Acquisition-PDM の形（版の出どころは VERSION）。"""

    def test_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r = Repo(Path(td) / "repo")
            r.commit("first", {"VERSION": "0.3.0\n"})
            r.tag("v0.3.0")
            args = ("check", "--repo-root", str(r.root), "--tag", "v0.3.0", "--version-source",
                    "VERSION", "--first-version", "0.3.0", "--main-ref", "main")
            self.assertEqual(run(*args)[0], 0)
            r.commit("two lines", {"VERSION": "0.3.1\nextra\n"})
            r.tag("v0.3.1")
            code, out = run("check", "--repo-root", str(r.root), "--tag", "v0.3.1", "--version-source",
                            "VERSION", "--first-version", "0.3.0", "--main-ref", "main")
            self.assertEqual(code, 1, out)


class ReleaseListTest(unittest.TestCase):

    def test_paginated_arrays_and_json_lines(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text('[{"tag_name": "v1.0.0"}]\n[{"tag_name": "v1.0.1"}]', encoding="utf-8")
            self.assertEqual(len(ric.load_releases(str(p)) or []), 2)
            p.write_text('{"tag_name": "v1.0.0"}\n{"tag_name": "v1.0.1"}\n', encoding="utf-8")
            self.assertEqual(len(ric.load_releases(str(p)) or []), 2)
            p.write_text("", encoding="utf-8")
            self.assertEqual(ric.load_releases(str(p)), [])


class ItIsWiredIntoCiTest(unittest.TestCase):

    def test_the_workflow_runs_this_test(self) -> None:
        wf = (HERE.parents[1] / ".github" / "workflows" / "basic-checks.yml").read_text(encoding="utf-8")
        self.assertIn("test_release_identity.py", wf)


if __name__ == "__main__":
    unittest.main()

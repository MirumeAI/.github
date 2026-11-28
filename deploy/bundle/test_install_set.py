"""新しい形を機体へ USB で運ぶ一式を作る・照らす道具（install_set.py）の試験。偽の Git の Repository で本物の
git を流し、偽の束（入れる道具の試験と同じ形）で、作った一式に入れる道具の入れる前の照らしが通ることを照らす。
一式の中の道具は、機体と同じ命令で別の処理として流す（CI の -B に隠れないように、子には -B を渡さない形も流す）。
偽の案件（fake-case）は、根の pyproject.toml の [tool.mirumeai].install-set-extra で根の物（docs）を足す。案件に依らない
共通の試験（正本は MirumeAI/.github の同じ道。MirumeAI/.github#237）。この Repository の物を照らす試験は、案件の
Repository でだけ流す。"""
from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _load(name: str, as_name: str):
    spec = importlib.util.spec_from_file_location(as_name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IS = _load("install_set", "_t_install_set")
PB = _load("make_product_bundle", "_t_make_product_bundle")
TV = _load("test_install_venvs", "_t_install_venvs_helpers")   # 偽の束の作り方（make_bundle・sha・wheel）と入れる道具
GIT = ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", "-c", "init.defaultBranch=main",
       "-c", "commit.gpgsign=false"]
DIRS = {".github", "acceptance", "config", "deploy", "deployment", "docs", "evaluation", "machines", "recipes", "release",
        "requirements", "scripts", "src", "tests", "validation"}
#: 偽の案件が足す根の物
EXTRA = ("docs",)
FAKE_PYPROJECT = '[project]\nname = "fake-case"\ndynamic = ["version"]\n\n[tool.mirumeai]\ninstall-set-extra = ["docs"]\n'
#: この Repository が案件の Repository か（正本〔MirumeAI/.github〕の根には pyproject.toml が無い）
IN_CASE = TV.IN_CASE


def git(repo: Path, *args: str) -> str:
    return subprocess.run(GIT + ["-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def clean_env() -> "dict[str, str]":
    return {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}


def pycaches(top: Path) -> "list[str]":
    return [p.relative_to(top).as_posix() for p in top.rglob("__pycache__")]


class InstallSet(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.case, self.wh = self.tmp / "case", self.tmp / "case" / "wheelhouse"
        self.repo = self.case / "repo-297"
        (self.tmp / "usb").mkdir()
        self.out = self.tmp / "usb" / "set1"
        self.repo.mkdir(parents=True)
        git(self.repo, "init", "-q")
        git(self.repo, "remote", "add", "origin", "https://example.invalid/org/fake-case.git")
        for e in IS.TREE_TOP + EXTRA:
            p = self.repo / e
            if e in DIRS:
                p.mkdir()
                (p / "keep.txt").write_text(f"# {e}\n", encoding="utf-8")
            elif e not in ("versions.yaml", "pyproject.toml"):
                p.write_text(f"# {e}\n", encoding="utf-8")
        (self.repo / "deploy" / "bundle").mkdir()
        for t in IS.TOOLS:                                              # 木の commit の道具は、流す道具と同じ
            shutil.copyfile(HERE / t, self.repo / "deploy" / "bundle" / t)
        (self.repo / "pyproject.toml").write_text(FAKE_PYPROJECT, encoding="utf-8")
        (self.repo / "src" / "fake_case").mkdir()
        (self.repo / "src" / "fake_case" / "__init__.py").write_text('__version__ = "0.1.0"\n', encoding="utf-8")
        (self.repo / "notes").mkdir()                                   # 並びに無い根の物（一式に入らない）
        (self.repo / "notes" / "x.txt").write_text("x\n", encoding="utf-8")
        (self.repo / "deploy" / "notes").mkdir()                        # 並べた物の下の同じ名の物（一式に入る）
        (self.repo / "deploy" / "notes" / "x.txt").write_text("x\n", encoding="utf-8")
        pdm_sums, pdm_wheel = TV.make_bundle(
            self.wh / "pdm" / "v2.0.0",
            {"mirumeai_pdm-2.0.0-cp310-cp310-linux_x86_64.whl": b"pdm", "numpy-2.2.6-cp310-cp310-linux_x86_64.whl": b"np"},
            "mirumeai_pdm-2.0.0-cp310-cp310-linux_x86_64.whl",
            {"product": "pdm", "version": "2.0.0", "tag": "v2.0.0", "commit": TV.PDM_COMMIT})
        lock = "fastapi==0.128.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
        ia_sums, ia_wheel = TV.make_bundle(
            self.wh / "ia" / "v1.0.0",
            {"mirumeai_image_acquisition-1.0.0-py3-none-any.whl": b"ia", "fastapi-0.128.0-py3-none-any.whl": b"fa"},
            "mirumeai_image_acquisition-1.0.0-py3-none-any.whl",
            {"product": "image-acquisition-pdm", "version": "1.0.0", "tag": "v1.0.0", "commit": TV.IA_COMMIT,
             "pdm": {"version": "2.0.0", "tag": "v2.0.0", "sha256sums_sha256": pdm_sums}},
            files={"requirements-web.lock": lock, "requirements-guard.lock": lock})
        (self.repo / "versions.yaml").write_text(
            "schema_version: 2\n"
            f"image_acquisition_pdm:\n  tag: v1.0.0\n  commit: {TV.IA_COMMIT}\n  sha256sums_sha256: {ia_sums}\n"
            f"  wheel_sha256: {ia_wheel}\n"
            f"pdm:\n  tag: v2.0.0\n  commit: {TV.PDM_COMMIT}\n  sha256sums_sha256: {pdm_sums}\n"
            f"  wheel_sha256: {pdm_wheel}\n", encoding="utf-8")
        self.commit = self.to_main("一式の試験")
        self.make_product(self.commit)

    def commit_all(self, msg: str) -> str:
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", msg)
        return git(self.repo, "rev-parse", "HEAD").strip()

    def to_main(self, msg: str) -> str:
        head = self.commit_all(msg)
        git(self.repo, "update-ref", "refs/remotes/origin/main", head)
        return head

    def make_product(self, commit: str, version: str = "0.1.0") -> None:
        TV.make_bundle(self.wh / "fake-case" / version, {f"fake_case-{version}-py3-none-any.whl": b"product"},
                       f"fake_case-{version}-py3-none-any.whl",
                       {"product": "fake-case", "version": version, "commit": commit})

    def main(self, *args: str) -> "tuple[int, str, str]":
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = IS.main(list(args), repo=self.repo)
        return rc, out.getvalue(), err.getvalue()

    def make(self, *extra: str) -> "tuple[int, str, str]":
        return self.main("make", "--out", str(self.out), "--label", "T-1", "--wheelhouse", str(self.wh), *extra)

    def made(self, *extra: str) -> str:
        rc, out, err = self.make(*extra)
        self.assertEqual(rc, 0, out + err)
        return re.search(r"SHA256SUMS の sha256: ([0-9a-f]{64})", out).group(1)

    def refuses(self, says: str, *extra: str) -> str:
        rc, out, err = self.make(*extra)
        self.assertEqual(rc, 1, out + err)
        self.assertIn(says, err)
        self.assertFalse(self.out.exists(), "止めたのに一式を残した")
        self.assertEqual(sorted(p.name for p in (self.tmp / "usb").iterdir()), [], "止めたのに途中の物を残した")
        return err

    def verify(self, sums: str, *extra: str, top: "Path | None" = None) -> "tuple[int, str, str]":
        return self.main("verify", str(top or self.out), "--sums-sha256", sums, *extra)

    def run_set_tool(self, sums: str, *flags: str) -> "subprocess.CompletedProcess":
        """一式の中の道具を、機体と同じ命令で別の処理として流す（一式の根で、相対の道で）。"""
        return subprocess.run([sys.executable, *flags, "fake-case/deploy/bundle/install_set.py", "verify", ".",
                               "--sums-sha256", sums], cwd=self.out, capture_output=True, text=True, env=clean_env())

    # ---- 作る

    def test_makes_the_set_and_it_passes_the_install_checks(self):
        rc, out, err = self.make()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("木の中の入れる道具の入れる前の照らし", out)
        self.assertIn("カメラの部品: なし", out)
        tree = self.out / "fake-case"
        self.assertEqual(sorted(p.name for p in tree.iterdir()), sorted(IS.TREE_TOP + EXTRA))   # 並べた物だけ・.git は無い
        self.assertTrue((tree / "deploy" / "notes" / "x.txt").is_file())   # 並べた物の下は、根で外す名の物も入る
        for d in ("wheelhouse/pdm/v2.0.0", "wheelhouse/ia/v1.0.0", "wheelhouse/fake-case/0.1.0"):
            self.assertTrue((self.out / d / "SHA256SUMS").is_file(), d)
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), ["SHA256SUMS", "fake-case", "install-set.json",
                                                                      "wheelhouse"])
        self.assertEqual(sorted(p.name for p in (self.tmp / "usb").iterdir()), ["set1"])  # 仮の物を残さない
        self.assertEqual(pycaches(self.out), [])
        mark = json.loads((self.out / "install-set.json").read_text(encoding="utf-8"))
        self.assertEqual((mark["label"], mark["test"], mark["repository"]),
                         ("T-1", False, {"name": "fake-case", "commit": self.commit}))
        self.assertEqual((mark["cores"]["pdm"]["tag"], mark["cores"]["ia"]["tag"]), ("v2.0.0", "v1.0.0"))
        self.assertEqual((mark["product"]["version"], mark["product"]["commit"], mark["camera"]), ("0.1.0", self.commit, []))
        # SHA256SUMS は一式の全部のファイル（自身を除く）
        listed = {l.split("  ", 1)[1] for l in (self.out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()}
        files = {p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()} - {"SHA256SUMS"}
        self.assertEqual(listed, files)
        sums = re.search(r"SHA256SUMS の sha256: ([0-9a-f]{64})", out).group(1)
        self.assertEqual(sums, TV.sha((self.out / "SHA256SUMS").read_bytes()))
        self.assertIn('wheel_sha256: "', out)
        # 作った一式で、機体と同じ入れる前の照らし（本物の機体の設定の形と System Release の形）が通る
        (tmp := self.tmp / "m").mkdir()
        (tmp / "M01.yaml").write_text("machine:\n  symbol: M01\n  kind: installed\n", encoding="utf-8")
        (tmp / "R1.yaml").write_text(f'product_package:\n  name: fake-case\n  version: "0.1.0"\n'
                                     f'  wheel_sha256: "{mark["product"]["wheel_sha256"]}"\n', encoding="utf-8")
        p = TV.IV.plan(tree, self.out / "wheelhouse", tmp / "M01.yaml", tmp / "R1.yaml")
        self.assertEqual(p["want"]["pdm"]["tag"], "v2.0.0")
        rc, out, err = self.verify(sums)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("一式は SHA256SUMS のとおりです", out)

    def test_a_default_entry_the_tree_lacks_is_left_out(self):
        """既定の並びのうち木に無い物は入れず、名前を出す（雛形より前に作った案件など。案件が足した物は、無ければ止める）。"""
        git(self.repo, "rm", "-q", "-r", "requirements")
        self.to_main("既定の並びの物を外した")
        rc, out, err = self.make()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("機体に置く物の既定の並びのうち、木に無い物（入れていない）: requirements", out)
        self.assertEqual(sorted(p.name for p in (self.out / "fake-case").iterdir()),
                         sorted(set(IS.TREE_TOP + EXTRA) - {"requirements"}))

    def test_refuses_what_the_declaration_of_the_commit_adds_wrongly(self):
        """足す物は commit の根の pyproject.toml から読み（作業ツリーの物は読まない）、根に無い・開発の機体だけの物・読めない
        宣言なら作らない。"""
        pyproject = self.repo / "pyproject.toml"
        pyproject.write_text(FAKE_PYPROJECT.replace('["docs"]', '["docs", "nothing"]'), encoding="utf-8")
        self.made()                                                         # commit していない作業ツリーの物は読まない
        shutil.rmtree(self.out)
        for text, says in ((FAKE_PYPROJECT.replace('["docs"]', '["docs", "nothing"]'),
                            "commit の根に、install-set-extra に書いた物がありません: nothing"),
                           (FAKE_PYPROJECT.replace('["docs"]', '["docs", "dev"]'), "開発の機体だけの物"),
                           (FAKE_PYPROJECT.replace('["docs"]', '["docs", "deploy/bundle"]'), "根の 1 つの名前ではありません"),
                           (FAKE_PYPROJECT + "[tool.mirumeai]\n", "pyproject.toml を読めません"),
                           ('[project]\nname = "fake-case"\n[tool.mirumeai]\ninstall-set = ["docs"]\n', "読まない鍵があります")):
            with self.subTest(says=says):
                pyproject.write_text(text, encoding="utf-8")
                self.to_main("宣言を変えた")
                self.refuses(says)
        git(self.repo, "rm", "-q", "pyproject.toml")
        self.to_main("宣言を消した")
        self.refuses("根に pyproject.toml がありません")

    def test_refuses_a_commit_not_in_main_unless_it_is_a_test_set(self):
        (self.repo / "README.md").write_text("# 変えた\n", encoding="utf-8")
        head = self.commit_all("main に入っていない")
        self.refuses("origin/main に入っていません", "--commit", head)
        # `origin/main` という名前のローカルの Branch とタグは読まない（追跡の ref を名指しする）
        for kind in ("branch", "tag"):
            with self.subTest(kind=kind):
                git(self.repo, kind, "origin/main", head)
                try:
                    self.refuses("origin/main に入っていません", "--commit", head)
                finally:
                    git(self.repo, kind, "-d", "origin/main")
        sums = self.made("--commit", head, "--test")
        mark = json.loads((self.out / "install-set.json").read_text(encoding="utf-8"))
        self.assertEqual((mark["test"], mark["repository"]["commit"]), (True, head))
        rc, out, err = self.verify(sums)                                  # 機体の verify は試しの一式を断る
        self.assertEqual((rc, "試しの一式です" in err), (1, True), err)
        rc, out, err = self.verify(sums, "--allow-test")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("試しの一式（機体へ入れない）", out)

    def test_refuses_tools_that_differ_from_the_commit_unless_it_is_a_test_set(self):
        tool = self.repo / "deploy" / "bundle" / "install_venvs.py"
        tool.write_text(tool.read_text(encoding="utf-8") + "# 違う道具\n", encoding="utf-8")
        self.to_main("木の道具だけ変えた")
        self.refuses("一式を作る道具が、木の commit の物と違います: install_venvs.py")
        self.made("--test")

    def test_refuses_where_it_cannot_make(self):
        self.out.mkdir()
        rc, out, err = self.make()
        self.assertEqual((rc, "作る先がもうあります" in err), (1, True))
        self.out.rmdir()
        self.out.with_name("set1.partial").mkdir()
        rc, out, err = self.make()
        self.assertEqual((rc, "前の途中の物があります" in err), (1, True))
        self.out.with_name("set1.partial").rmdir()
        for where, says in ((self.repo / "inside", "Git の作業ツリーの中"), (self.wh / "inside", "束の置き場の中")):
            rc, out, err = self.main("make", "--out", str(where), "--label", "T-1", "--wheelhouse", str(self.wh))
            self.assertEqual((rc, says in err), (1, True), err)
        self.refuses("一式の名前は", "--label", "../x")

    def test_refuses_a_product_bundle_made_from_other_product_files(self):
        (self.repo / "src" / "fake_case" / "x.py").write_text("y = 1\n", encoding="utf-8")
        self.to_main("製品のファイルを変えた")
        self.refuses("製品の wheel の入力")

    def test_refuses_a_product_bundle_when_other_wheel_inputs_changed(self):
        (self.repo / "setup.cfg").write_text("[metadata]\n", encoding="utf-8")
        self.to_main("wheel の入力になる根のファイルを足した")
        self.refuses("製品の wheel の入力")

    def test_a_change_outside_the_product_files_keeps_the_product_bundle(self):
        (self.repo / "docs" / "keep.txt").write_text("# 文書だけ\n", encoding="utf-8")
        head = self.to_main("文書だけ")
        self.made()
        mark = json.loads((self.out / "install-set.json").read_text(encoding="utf-8"))
        self.assertEqual((mark["repository"]["commit"], mark["product"]["commit"]), (head, self.commit))

    def test_refuses_a_product_bundle_that_is_missing_or_not_this_product(self):
        shutil.rmtree(self.wh / "fake-case")
        self.refuses("製品の束がありません")
        self.make_product(self.commit)
        rel = self.wh / "fake-case" / "0.1.0" / "release.json"
        data = json.loads(rel.read_text(encoding="utf-8"))
        rel.write_text(json.dumps(dict(data, version="0.2.0")), encoding="utf-8")
        self.refuses("名前・版が、木の宣言と違います")
        rel.write_text(json.dumps(dict(data, commit="d" * 40)), encoding="utf-8")
        self.refuses("この Repository にありません")

    def test_refuses_a_core_bundle_that_does_not_match_versions(self):
        (self.wh / "pdm" / "v2.0.0" / "wheelhouse" / "numpy-2.2.6-cp310-cp310-linux_x86_64.whl").write_bytes(b"other")
        self.refuses("SHA256SUMS と違います")

    def test_copies_only_what_the_bundle_lists(self):
        (self.wh / "ia" / "v1.0.0" / "notes.txt").write_text("人が置いたメモ\n", encoding="utf-8")
        self.refuses("束の SHA256SUMS に無いファイルがあります: notes.txt")

    def test_refuses_links_in_what_it_copies(self):
        (self.wh / "ia" / "v1.0.0" / "link").symlink_to("README.md")
        self.refuses("リンク")
        (self.wh / "ia" / "v1.0.0" / "link").unlink()
        (self.wh / "ia" / "v1.0.0" / "dirlink").symlink_to("wheelhouse")
        self.refuses("リンクがあります: dirlink")

    def test_refuses_links_and_bad_names_in_the_tree(self):
        (self.repo / "docs" / "link").symlink_to("keep.txt")
        self.to_main("木にリンク")
        self.refuses("リンクか外を指す道")
        (self.repo / "docs" / "link").unlink()
        (self.repo / "docs" / "Keep.txt").write_text("x\n", encoding="utf-8")
        self.to_main("大文字と小文字だけが違う名前")
        self.refuses("大文字と小文字だけが違う名前")
        (self.repo / "docs" / "Keep.txt").unlink()
        (self.repo / "docs" / "a:b.txt").write_text("x\n", encoding="utf-8")
        self.to_main("FAT 系で使えない名前")
        self.refuses("USB の FAT 系で使えない名前")

    def test_the_tree_is_the_commit_whatever_the_local_git_state(self):
        """手元の属性の export-ignore と filter・Repository の設定の filter・置き換えの ref・環境の GIT_CONFIG_COUNT が
        あっても、一式の木は commit の blob そのまま（git archive はこれらに従う）。外に GIT_DIR・GIT_INDEX_FILE を
        置いても、道具のどの git にも絞った環境が渡り、一式を作れる。"""
        info = self.repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text("docs/keep.txt export-ignore\nREADME.md filter=evil\nCLAUDE.md filter=env\n",
                                         encoding="utf-8")
        git(self.repo, "config", "filter.evil.smudge", "sed s/README/evil/")
        blob = git(self.repo, "rev-parse", "HEAD:acceptance/keep.txt").strip()
        fake = subprocess.run(GIT + ["-C", str(self.repo), "hash-object", "-w", "--stdin"], input="置き換え\n",
                              capture_output=True, text=True, check=True).stdout.strip()
        git(self.repo, "replace", blob, fake)
        outer = dict(os.environ, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="filter.env.smudge",
                     GIT_CONFIG_VALUE_0="sed s/CLAUDE/evil/", GIT_DIR=str(self.tmp / "elsewhere"),
                     GIT_INDEX_FILE=str(self.tmp / "no-index"))
        with mock.patch.dict(os.environ, outer, clear=True):
            self.made()
        tree = self.out / "fake-case"
        self.assertEqual((tree / "docs" / "keep.txt").read_text(encoding="utf-8"), "# docs\n")
        self.assertEqual((tree / "README.md").read_text(encoding="utf-8"), "# README.md\n")
        self.assertEqual((tree / "CLAUDE.md").read_text(encoding="utf-8"), "# CLAUDE.md\n")
        self.assertEqual((tree / "acceptance" / "keep.txt").read_text(encoding="utf-8"), "# acceptance\n")
        self.assertFalse((tree / "notes").exists())                                 # 並びに無い根の物は入れない

    def test_the_place_inside_a_work_tree_is_refused_with_the_narrowed_environment(self):
        """作る先が Git の作業ツリーの中なら、外に GIT_DIR を置いても断る（照らしに絞った環境が渡る）。git が「Git の
        Repository でない」のほかの訳で止まったとき（持ち主の違う Repository など）も、中として断る。"""
        inner = self.repo / "docs" / "set1"
        with mock.patch.dict(os.environ, dict(os.environ, GIT_DIR=str(self.tmp / "elsewhere")), clear=True):
            rc, out, err = self.main("make", "--out", str(inner), "--label", "T-1", "--wheelhouse", str(self.wh))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("Git の作業ツリーの中", err)
        self.assertFalse(inner.exists())
        outside = self.tmp / "usb"
        self.assertFalse(IS.inside_work_tree(outside))
        for stderr, inside in (("fatal: detected dubious ownership in repository at '/x'\n", True),
                               ("fatal: not a git repository (or any of the parent directories): .git\n", False)):
            with mock.patch.object(IS.subprocess, "run", return_value=subprocess.CompletedProcess([], 128, "", stderr)):
                self.assertEqual(IS.inside_work_tree(outside), inside, stderr)

    def test_links_outside_the_tree_list_are_not_copied_and_do_not_stop(self):
        """機体に置く物の並びの外のリンクは、写さず、止めもしない（前の git archive の並びと同じ範囲）。"""
        (self.repo / "notes" / "link").symlink_to("x.txt")
        self.to_main("並びの外のリンク")
        self.made()
        self.assertFalse((self.out / "fake-case" / "notes").exists())

    def test_git_gets_the_same_narrowed_environment_as_the_product_bundle_tool(self):
        """一式の道具の git_env は、製品の束を作る道具の git_env と同じ形。"""
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_DIR": "/elsewhere", "HOME": "/home/u"}, clear=True):
            self.assertEqual(IS.git_env(), PB.git_env())
            self.assertFalse({"GIT_CONFIG_COUNT", "GIT_DIR"} & set(IS.git_env()))

    def test_refuses_a_missing_core_bundle(self):
        shutil.rmtree(self.wh / "ia" / "v1.0.0")
        self.refuses("ia v1.0.0 の束がありません")

    def test_camera_wheels_the_person_placed_are_copied_and_recorded(self):
        (self.wh / "camera").mkdir()
        data = TV.InstallVenvs.wheel()
        (self.wh / "camera" / "fakecam-1.2.3-py3-none-any.whl").write_bytes(data)
        rc, out, err = self.make()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual((self.out / "wheelhouse" / "camera" / "fakecam-1.2.3-py3-none-any.whl").read_bytes(), data)
        mark = json.loads((self.out / "install-set.json").read_text(encoding="utf-8"))
        self.assertEqual(mark["camera"], [{"name": "fakecam", "version": "1.2.3", "sha256": TV.sha(data),
                                           "file": "fakecam-1.2.3-py3-none-any.whl"}])
        self.assertIn(f"カメラの部品: fakecam 1.2.3・sha256 {TV.sha(data)}", out)

    def test_refuses_what_the_install_tool_refuses_in_camera(self):
        (self.wh / "camera").mkdir()
        (self.wh / "camera" / "notes.txt").write_text("x", encoding="utf-8")
        self.refuses("wheel でない物")

    def test_names_that_do_not_fit_fat_usb(self):
        for bad in ("A", "x\ny", " lead", "trail ", "dot.", "q?.txt"):
            d = self.tmp / "names"
            (d / "a").mkdir(parents=True)
            (d / bad if bad == "A" else d / "a" / bad).mkdir()
            with self.assertRaises(IS.Stop, msg=bad):
                IS.check_names(d)
            shutil.rmtree(d)

    # ---- 照らす

    def test_verify_refuses_changes(self):
        sums = self.made()
        rc, out, err = self.verify("0" * 64)
        self.assertEqual((rc, "渡した値と違います" in err), (1, True))
        target = self.out / "fake-case" / "versions.yaml"
        original = target.read_bytes()
        target.write_bytes(original + "# 書き換え\n".encode())
        rc, out, err = self.verify(sums)
        self.assertEqual((rc, "SHA256SUMS と違います" in err), (1, True))
        target.write_bytes(original)
        cases = [(self.out / "fake-case" / "deploy" / "added.py", "SHA256SUMS に無いファイル"),
                 (self.out / "wheelhouse" / "added.whl", "SHA256SUMS に無いファイル"),
                 (self.out / "added.txt", "根に、SHA256SUMS に無い物")]
        for path, says in cases:
            path.write_text("x", encoding="utf-8")
            rc, out, err = self.verify(sums)
            self.assertEqual((rc, says in err), (1, True), (path, err))
            path.unlink()
        link = self.out / "wheelhouse" / "ia" / "v1.0.0" / "lnk"
        link.symlink_to("README.md")
        rc, out, err = self.verify(sums)
        self.assertEqual((rc, "リンク" in err), (1, True), err)
        link.unlink()
        target.unlink()
        rc, out, err = self.verify(sums)
        self.assertEqual((rc, "無いか、リンクです" in err), (1, True), err)
        target.write_bytes(original)
        self.assertEqual(self.verify(sums)[0], 0)

    def test_verify_before_install_refuses_root_dirs_it_did_not_bring(self):
        """入れる前の一式に .venvs/ などがあれば断る（届いた venv は一式の sha256 の外で、入れる道具がそのまま使いうる）。"""
        sums = self.made()
        (self.out / ".venvs" / "web").mkdir(parents=True)
        rc, out, err = self.verify(sums)
        self.assertEqual((rc, "根に、SHA256SUMS に無い物があります: .venvs" in err), (1, True), err)
        (self.out / ".cache").mkdir()
        (self.out / "work").mkdir()
        (self.out / "work" / "data.txt").write_text("x", encoding="utf-8")
        rc, out, err = self.verify(sums, "--installed")                   # 入れた後に照らし直すときだけ、見ずに名前を出す
        self.assertEqual(rc, 0, out + err)
        self.assertIn("見なかった根の dir: .venvs/（入れた後にできる物。中は照らしていない）", out)
        self.assertIn("見なかった根の dir: work/（一式の外の物。中は照らしていない）", out)
        (self.out / "lnk").symlink_to("work")
        rc, out, err = self.verify(sums, "--installed")
        self.assertEqual((rc, "根にリンク" in err), (1, True))

    def test_verify_refuses_a_set_without_the_version_mark(self):
        d = self.tmp / "bare"
        d.mkdir()
        (d / "a.txt").write_text("x", encoding="utf-8")
        (d / "SHA256SUMS").write_text(f"{TV.sha(b'x')}  a.txt\n", encoding="utf-8")
        rc, out, err = self.verify(TV.sha((d / "SHA256SUMS").read_bytes()), top=d)
        self.assertEqual((rc, "版の印" in err), (1, True))

    # ---- 一式の中の道具を、機体と同じ命令で流す

    def test_the_tool_in_the_set_verifies_as_documented_and_writes_nothing(self):
        sums = self.made()
        r = self.run_set_tool(sums, "-I", "-B")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("一式は SHA256SUMS のとおりです", r.stdout)
        self.assertEqual(pycaches(self.out), [])

    def test_the_tool_stops_before_reading_anything_without_isolation(self):
        """-I -B を付けずに流すと、隣の物を読まず、一式に何も書かずに止める（足された同じ名前の物も流れない）。"""
        sums = self.made()
        marker = self.tmp / "ran"
        planted = self.out / "fake-case" / "deploy" / "bundle" / "argparse.py"
        planted.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        for flags in ((), ("-I",), ("-B",)):
            r = self.run_set_tool(sums, *flags)
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("python3 -I -B で流してください", r.stderr)
        r = self.run_set_tool(sums, "-I", "-B")                             # 付けると読まずに、足した物を断る
        self.assertEqual((r.returncode, "SHA256SUMS に無いファイル" in r.stderr), (1, True), r.stderr)
        self.assertFalse(marker.exists(), "足した物が流れた")
        self.assertEqual(pycaches(self.out), [])

    def test_a_planted_pyc_of_a_neighbour_tool_does_not_run(self):
        """verify は隣の道具を読まないので、足された __pycache__ の pyc は流れず、足した物として断る。"""
        sums = self.made()
        marker = self.tmp / "ran"
        src = self.tmp / "evil.py"
        src.write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        neighbour = self.out / "fake-case" / "deploy" / "bundle" / "install_venvs.py"
        py_compile.compile(str(src), cfile=importlib.util.cache_from_source(str(neighbour)), doraise=True,
                           invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)
        r = self.run_set_tool(sums, "-I", "-B")
        self.assertEqual((r.returncode, "SHA256SUMS に無いファイル" in r.stderr), (1, True), r.stderr)
        self.assertFalse(marker.exists(), "足した pyc が流れた")


class TreeList(unittest.TestCase):
    """機体に置く根の物の並び（既定の並び TREE_TOP と、案件が根の pyproject.toml の install-set-extra で足す物）。"""

    def test_the_default_list_is_one_literal_without_repeats_or_dev_only_things(self):
        """既定の並びは頭の 1 つの代入の字のままで、ほかで足さない・変えない。重なりと、開発の機体だけの物が無い（雛形の根と
        合うことは、正本の新しい案件を作る道具の試験が照らす）。"""
        tree = ast.parse((HERE / "install_set.py").read_text(encoding="utf-8"))
        writes = [n for n in ast.walk(tree) if (isinstance(n, ast.Name) and n.id == "TREE_TOP"
                                                and not isinstance(n.ctx, ast.Load))
                  or (isinstance(n, (ast.Global, ast.Nonlocal)) and "TREE_TOP" in n.names)]
        self.assertEqual(len(writes), 1, [ast.dump(n) for n in writes])
        sets = [n for n in tree.body if isinstance(n, ast.Assign) and writes[0] in n.targets]
        self.assertEqual(len(sets), 1)
        self.assertEqual(ast.literal_eval(sets[0].value), IS.TREE_TOP)
        self.assertEqual(len(IS.TREE_TOP), len(set(IS.TREE_TOP)))
        self.assertEqual(sorted(set(IS.TREE_TOP) & set(IS.DEV_ONLY)), [])
        self.assertEqual([e for e in IS.TREE_TOP if e.startswith(IS.PINS_PREFIX)], ["versions.yaml"])

    def test_the_tree_is_written_with_the_list_from_the_declaration(self):
        """木を出す copy_tree は 2 つだけ: 宣言を先に読む pyproject.toml と、tree_list が決めた並び（ほかの並びで出さない）。"""
        tree = ast.parse((HERE / "install_set.py").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and getattr(n.func, "attr", getattr(n.func, "id", None)) == "copy_tree"]
        self.assertEqual(len(calls), 2)
        tops = sorted(ast.dump(k.value) for c in calls for k in c.keywords if k.arg == "top")
        self.assertEqual(tops, sorted([ast.dump(ast.parse("('pyproject.toml',)", mode="eval").body),
                                       ast.dump(ast.Name(id="top", ctx=ast.Load()))]))
        extract = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "extract_tree"][0]
        sets = [n for n in ast.walk(extract) if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "top" for t in n.targets)]
        self.assertEqual([ast.dump(n.value.func) for n in sets], [ast.dump(ast.Name(id="tree_list", ctx=ast.Load()))])

    def test_what_a_case_adds_is_checked(self):
        def top(*extra):
            return IS.tree_list({"install_set_extra": list(extra)})
        self.assertEqual(top(), IS.TREE_TOP)
        self.assertEqual(top("docs", ".ci-imports-allow"), IS.TREE_TOP + ("docs", ".ci-imports-allow"))
        for extra, says in ((("a/b",), "根の 1 つの名前ではありません"), ((".",), "根の 1 つの名前ではありません"),
                            (("..",), "根の 1 つの名前ではありません"), (("",), "根の 1 つの名前ではありません"),
                            (("a b",), "根の 1 つの名前ではありません"), (("a\\b",), "根の 1 つの名前ではありません"),
                            ((".git",), ".git は書けません"), ((".GIT",), ".git は書けません"),
                            (("src",), "既定の並びにあります"), (("versions.yaml",), "既定の並びにあります"),
                            ((".claude",), "開発の機体だけの物"), (("dev",), "開発の機体だけの物"),
                            (("versions.local.yaml",), "開発の機体だけの物"), (("docs", "docs"), "同じ名前が 2 度")):
            with self.subTest(extra=extra), self.assertRaisesRegex(IS.Stop, says):
                top(*extra)

    @unittest.skipUnless(IN_CASE, TV.NOT_IN_CASE)
    def test_every_root_entry_of_this_repository_is_decided(self):
        """この Repository の根の物は、機体に置く物の並び（既定の並びと、根の pyproject.toml で足した物）か、開発の機体だけの
        物のどちらかにある。足した物は根にある（根に物を足すときは、機体に要るかを決め、要るなら install-set-extra に書く）。"""
        declaration = TV.IV.read_declaration(REPO)
        top = IS.tree_list(declaration)
        files = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, text=True,
                               check=True).stdout.split("\0")
        roots = {f.split("/", 1)[0] for f in files if f}
        dev = {r for r in roots if r in IS.DEV_ONLY or (r.startswith(IS.PINS_PREFIX) and r not in IS.TREE_TOP)}
        self.assertEqual(sorted(roots - set(top) - dev), [])
        self.assertEqual(sorted(set(declaration["install_set_extra"]) - roots), [])

    def test_the_tools_are_in_the_tree(self):
        self.assertIn("deploy", IS.TREE_TOP)
        self.assertIn("pyproject.toml", IS.TREE_TOP)
        for t in IS.TOOLS:
            self.assertTrue((HERE / t).is_file(), t)


if __name__ == "__main__":
    unittest.main()

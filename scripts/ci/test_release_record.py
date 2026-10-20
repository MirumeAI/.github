#!/usr/bin/env python3
"""`release_record_check.py` が、 記録の食い違いを見つけることを確かめる。

**項目の有無だけでは足りない。** 記録どうしが食い違っていても、 書いて
あるように見えてしまう。 どれも「必要になった瞬間まで気づけない」形。

    deployed_release  に書いた版が release/ に無い
    rollback_release  に書いた版が無い（**戻せない**）
    rollback_release  が deployed_release と同じ（戻す先が今の版）
    release_id        がファイル名と違う（どちらが正しいか分からない）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "release_record_check.py"

RELEASE_NEW = """schema_version: 1
release_id: PJ999-R2026.10.1
software: {versions_ref: versions.yaml, versions_commit: abc1234}
inspection: {version: inspection-v12, model_sha256: deadbeef,
             config_commit: abc1234, calibration: 良品100枚}
dataset: {training: DS-v3, evaluation: DS-eval-v2, manifest_sha256: cafebabe}
runtime: {os: Ubuntu 22.04, python: "3.10"}
hardware: {cameras: 5}
validation: {report: r.md, acceptance: a.md, smoke_test: 確認済}
"""
RELEASE_OLD = RELEASE_NEW.replace("PJ999-R2026.10.1", "PJ999-R2026.09.2")
DEPLOYMENT = """schema_version: 1
deployed_at: 2026-10-04T09:30:00+09:00
target: {site: site-a, line: line-1, device: dev-1}
deployed_release: PJ999-R2026.10.1
rollback_release: PJ999-R2026.09.2
smoke_test: {result: PASS, checked: [起動して判定が出る, PLCへ判定が返る]}
verified: {release_hash_matches_manifest: true, operator: 担当者}
"""


def _needs_yaml() -> bool:
    try:
        import yaml                                        # noqa: F401
        return False
    except ImportError:
        return True


class _Records(unittest.TestCase):
    """正しい組を置いて、 1 か所ずつ崩す。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "release").mkdir()
        (self.root / "deployment").mkdir()
        self.new = self.root / "release" / "PJ999-R2026.10.1.yaml"
        self.old = self.root / "release" / "PJ999-R2026.09.2.yaml"
        self.dep = self.root / "deployment" / "line-1-2026-10-04.yaml"
        self.new.write_text(RELEASE_NEW, encoding="utf-8")
        self.old.write_text(RELEASE_OLD, encoding="utf-8")
        self.dep.write_text(DEPLOYMENT, encoding="utf-8")
        # **README は記録ではない。** 対象に入れると必ず落ちる。
        (self.root / "release" / "README.md").write_text("# r\n",
                                                         encoding="utf-8")
        (self.root / "deployment" / "README.md").write_text("# d\n",
                                                            encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_check(self, root: "Path | None" = None) -> tuple:
        env = dict(os.environ, CI_ROOT=str(root or self.root))
        r = subprocess.run([sys.executable, str(CHECKER)],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr


@unittest.skipIf(_needs_yaml(), "PyYAML が無い環境では記録を読めない")
class ItFindsTheMismatchesTest(_Records):

    def test_a_correct_pair_passes(self) -> None:
        code, out = self.run_check()
        self.assertEqual(code, 0, out[-400:])
        self.assertIn("release 2 件", out)
        self.assertIn("deployment 1 件", out)

    def test_a_deployed_release_that_does_not_exist(self) -> None:
        self.dep.write_text(
            DEPLOYMENT.replace("deployed_release: PJ999-R2026.10.1",
                               "deployed_release: PJ999-R9999.99.9"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("deployed_release", out)
        self.assertIn("release/ にありません", out)

    def test_a_rollback_release_that_does_not_exist(self) -> None:
        """**戻せない状態を通さないこと。**"""
        self.dep.write_text(
            DEPLOYMENT.replace("rollback_release: PJ999-R2026.09.2",
                               "rollback_release: PJ999-R0000.00.0"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("rollback_release", out)

    def test_a_rollback_equal_to_the_deployed_release(self) -> None:
        """戻す先が今の版では、 戻せていない。"""
        self.dep.write_text(
            DEPLOYMENT.replace("rollback_release: PJ999-R2026.09.2",
                               "rollback_release: PJ999-R2026.10.1"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("同じです", out)

    def test_a_missing_rollback_release(self) -> None:
        self.dep.write_text(
            "\n".join(ln for ln in DEPLOYMENT.splitlines()
                      if "rollback_release" not in ln) + "\n",
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("rollback_release がありません", out)

    def test_an_id_that_disagrees_with_the_filename(self) -> None:
        """どちらが正しいか分からなくなる。"""
        self.new.write_text(
            RELEASE_NEW.replace("release_id: PJ999-R2026.10.1",
                                "release_id: PJ999-RCHIGAU"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("ファイル名と違います", out)

    def test_a_free_text_smoke_result(self) -> None:
        self.dep.write_text(
            DEPLOYMENT.replace("result: PASS", "result: たぶん大丈夫"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("smoke_test.result", out)

    def test_an_unknown_schema_version(self) -> None:
        """**知らない形式を黙って受け付けないこと。**"""
        self.new.write_text(
            RELEASE_NEW.replace("schema_version: 1", "schema_version: 7"),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("知らない形式", out)

    def test_a_missing_field_inside_a_section(self) -> None:
        self.new.write_text(
            RELEASE_NEW.replace(", calibration: 良品100枚", ""),
            encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("inspection.calibration", out)

    def test_a_broken_yaml_is_reported_not_crashed(self) -> None:
        self.new.write_text("schema_version: 1\n  : : :\n", encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("読めません", out)


class ItDoesNothingWhereThereAreNoRecordsTest(unittest.TestCase):

    def _run(self, root: Path) -> tuple:
        env = dict(os.environ, CI_ROOT=str(root))
        r = subprocess.run([sys.executable, str(CHECKER)],
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stdout + r.stderr

    def test_no_folders_is_a_no_op(self) -> None:
        """Core や platform では何もしないこと。"""
        with tempfile.TemporaryDirectory() as td:
            code, out = self._run(Path(td))
            self.assertEqual(code, 0, out[-300:])
            self.assertIn("無いので何もしません", out)

    def test_empty_folders_pass(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "release").mkdir()
            (root / "deployment").mkdir()
            code, out = self._run(root)
            self.assertEqual(code, 0, out[-300:])


class ItNeverClaimsOkWithoutYamlTest(_Records):
    """**検査できないのに「合格」と言わないこと。**

    PyYAML が入っていない環境では記録を読めない。 そこで 0 を返すと、
    記録が壊れていても Pull Request が緑になる。 **黙って通るのが
    いちばん悪い。**

    PyYAML が入った環境でもこの経路を確かめるため、 import を塞いだ
    python で実行する（`sys.meta_path`）。
    """

    BLOCKER = """import sys
class _B:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] == 'yaml':
            raise ImportError("blocked")
        return None
sys.meta_path.insert(0, _B())
"""

    def _run_without_yaml(self, root: Path) -> tuple:
        with tempfile.TemporaryDirectory() as site:
            (Path(site) / "sitecustomize.py").write_text(
                self.BLOCKER, encoding="utf-8")
            env = dict(os.environ, CI_ROOT=str(root), PYTHONPATH=site)
            r = subprocess.run([sys.executable, str(CHECKER)],
                               capture_output=True, text=True, env=env)
            return r.returncode, r.stdout + r.stderr

    def test_records_present_but_unreadable_is_not_ok(self) -> None:
        code, out = self._run_without_yaml(self.root)
        self.assertEqual(code, 2, out[-400:])
        self.assertIn("検査できません", out)
        self.assertNotIn("OK:", out)

    def test_no_records_without_yaml_is_fine(self) -> None:
        """記録が無ければ、 読めなくても困らない。"""
        for f in (self.new, self.old, self.dep):
            f.unlink()
        code, out = self._run_without_yaml(self.root)
        self.assertEqual(code, 0, out[-400:])


VERSIONS_UNSELECTED = """schema_version: 2
image_acquisition_pdm: {repository: MirumeAI/Image-Acquisition-PDM, tag: null, commit: null}
pdm: {repository: MirumeAI/PDM, tag: null, commit: null}
"""


def _versions(ia=(None, None), pdm=(None, None), extra="") -> str:
    def v(x):
        return "null" if x is None else x
    return ("schema_version: 2\n"
            f"image_acquisition_pdm: {{repository: MirumeAI/Image-Acquisition-PDM, tag: {v(ia[0])}, commit: {v(ia[1])}}}\n"
            f"pdm: {{repository: MirumeAI/PDM, tag: {v(pdm[0])}, commit: {v(pdm[1])}}}\n" + extra)


@unittest.skipIf(_needs_yaml(), "PyYAML が無い環境では記録を読めない")
class VersionsYamlTest(unittest.TestCase):
    """`versions.yaml` は選択だけ。 **形の違う選択を黙って受け付けない**（P1-04）。"""

    A40, B40 = "a" * 40, "b" * 40

    def _run(self, text: str, env_extra: "dict | None" = None) -> tuple:
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "versions.yaml").write_text(text, encoding="utf-8")
            env = dict(os.environ, CI_ROOT=td, **(env_extra or {}))
            for k in ("CORE_ROOT_IMAGE_ACQUISITION_PDM", "CORE_ROOT_PDM"):
                if not (env_extra and k in env_extra):
                    env.pop(k, None)
            r = subprocess.run([sys.executable, str(CHECKER)],
                               capture_output=True, text=True, env=env)
            return r.returncode, r.stdout + r.stderr

    def test_unselected_passes(self) -> None:
        code, out = self._run(VERSIONS_UNSELECTED)
        self.assertEqual(code, 0, out)
        self.assertIn("versions.yaml: 未選択", out)

    def test_the_template_passes(self) -> None:
        tpl = (HERE.parents[1] / "templates" / "customer-project" / "versions.yaml")
        code, out = self._run(tpl.read_text(encoding="utf-8"))
        self.assertEqual(code, 0, out)
        self.assertIn("versions.yaml: 未選択", out)

    def test_a_selection_passes_the_form_and_says_what_it_did_not_check(self) -> None:
        code, out = self._run(_versions(("v1.2.3", self.A40), ("v0.4.0", self.B40)))
        self.assertEqual(code, 0, out)
        self.assertIn("Core の写しを", out)

    def test_the_old_form_is_read_but_not_judged(self) -> None:
        code, out = self._run("schema_version: 1\npdm: {repository: MirumeAI/PDM}\n")
        self.assertEqual(code, 0, out)
        self.assertIn("schema_version 1（古い形", out)

    def test_bad_selections_are_rejected(self) -> None:
        cases = {
            "schema_version: 3\n": "知らない形式",
            _versions(extra="migration: {status: x}\n"): "選択のほかの項目",
            _versions(("v1.2.3", None)): "commit は 40 桁",
            _versions((None, self.A40)): "tag は Product Release のタグ",
            _versions(("v1.2.3.dev0", self.A40)): "tag は Product Release のタグ",
            _versions(("v1.2.3", self.A40.upper())): "commit は 40 桁",
            _versions(pdm=("v0.4.0", self.B40)): "PDM だけを選べません",
            _versions().replace("MirumeAI/PDM", "MirumeAI/Other"): "pdm.repository は MirumeAI/PDM",
            _versions().replace("tag: null, commit: null}\npdm", "tag: null, commit: null, ref: main}\npdm"):
                "知らない項目",
        }
        for text, needle in cases.items():
            with self.subTest(needle=needle):
                code, out = self._run(text)
                self.assertEqual(code, 1, out)
                self.assertIn(needle, out)


@unittest.skipIf(_needs_yaml() or shutil.which("git") is None, "PyYAML か git が無い")
class VersionsAgainstCoreCopiesTest(unittest.TestCase):
    """Core の写しを渡したとき、 タグ・commit と、 PDM の行と撮像側の lock を照らす。"""

    GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
               "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(self, root: Path, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(root), "-c", "commit.gpgsign=false",
                            "-c", "tag.gpgsign=false", *args], capture_output=True, text=True,
                           env=dict(os.environ, **self.GIT_ENV), check=True)
        return r.stdout.strip()

    def commit(self, root: Path, name: str, body: str) -> str:
        (root / name).write_text(body, encoding="utf-8")
        self.git(root, "add", name)
        self.git(root, "commit", "-q", "-m", name)
        return self.git(root, "rev-parse", "HEAD")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.pdm, self.ia = base / "pdm", base / "ia"
        for r in (self.pdm, self.ia):
            r.mkdir()
            self.git(r, "init", "-q")
        self.p1 = self.commit(self.pdm, "a.txt", "1\n")
        self.git(self.pdm, "tag", "-a", "v1.2.3", "-m", "Product Release v1.2.3")
        self.p2 = self.commit(self.pdm, "a.txt", "2\n")
        self.git(self.pdm, "tag", "-a", "v1.2.4", "-m", "Product Release v1.2.4")
        self.git(self.pdm, "tag", "v1.2.5")                      # 注釈なし
        self.i1 = self.commit(self.ia, "a.txt", "1\n")
        self.git(self.ia, "tag", "-a", "v0.1.0", "-m", "Product Release v0.1.0")  # lock 無し
        lock = ('{"schema_version": 1, "pdm": {"version": "1.2.3", "tag": "v1.2.3", '
                f'"tag_object": "{"c" * 40}", "commit": "{self.p1}", '
                '"wheel": "pdm-1.2.3-cp310-cp310-linux_x86_64.whl", '
                f'"wheel_sha256": "{"d" * 64}", "sha256sums_sha256": "{"e" * 64}"}}}}\n')
        self.i2 = self.commit(self.ia, "pdm.lock.json", lock)
        self.git(self.ia, "tag", "-a", "v0.2.0", "-m", "Product Release v0.2.0")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_with_cores(self, text: str) -> tuple:
        return VersionsYamlTest._run(self, text, {"CORE_ROOT_IMAGE_ACQUISITION_PDM": str(self.ia),
                                                  "CORE_ROOT_PDM": str(self.pdm)})

    def test_a_matching_selection_passes(self) -> None:
        code, out = self.run_with_cores(_versions(("v0.2.0", self.i2), ("v1.2.3", self.p1)))
        self.assertEqual(code, 0, out)
        self.assertIn("Core の写しで照らした", out)
        code, out = self.run_with_cores(_versions(("v0.2.0", self.i2)))
        self.assertEqual(code, 0, out)

    def test_mismatches_are_rejected(self) -> None:
        cases = {
            _versions(("v0.2.0", self.i1), ("v1.2.3", self.p1)): "image_acquisition_pdm.commit が v0.2.0 の指す commit と違います",
            _versions(("v0.2.0", self.i2), ("v1.2.4", self.p2)): "撮像側の v0.2.0 の pdm.lock.json（v1.2.3）と違います",
            _versions(("v0.2.0", self.i2), ("v1.2.5", self.p2)): "注釈つきのタグとしてありません",
            _versions(("v0.1.0", self.i1), ("v1.2.3", self.p1)): "pdm.lock.json が無く",
            _versions(("v0.9.9", self.i2)): "注釈つきのタグとしてありません",
        }
        for text, needle in cases.items():
            with self.subTest(needle=needle):
                code, out = self.run_with_cores(text)
                self.assertEqual(code, 1, out)
                self.assertIn(needle, out)


class ItIsWiredIntoTheReusableWorkflowTest(unittest.TestCase):

    def test_the_workflow_calls_it(self) -> None:
        wf = (HERE.parents[1] / ".github" / "workflows"
              / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        self.assertIn("release_record_check.py", wf)


if __name__ == "__main__":
    unittest.main()

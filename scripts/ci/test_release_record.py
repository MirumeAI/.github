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


class ItIsWiredIntoTheReusableWorkflowTest(unittest.TestCase):

    def test_the_workflow_calls_it(self) -> None:
        wf = (HERE.parents[1] / ".github" / "workflows"
              / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        self.assertIn("release_record_check.py", wf)


if __name__ == "__main__":
    unittest.main()

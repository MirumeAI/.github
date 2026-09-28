#!/usr/bin/env python3
"""`org_audit.py` の判定が正しいことを確かめる。

**ネットワークを使わない。** 監査の入口（`gh api`）は差し替えて、
所見の組み立てだけを見る。 CI で毎回 Organization を叩くと、 権限や
Plan の状態で結果が変わり、 テストが信用できなくなる。

ここで確かめるのは 3 つの区別が崩れないこと。

  FINDING    標準と違い、 直せるもの
  EXCEPTION  Plan / 権限の制約で実施できないもの（**省略ではない**）
  UNKNOWN    確かめられなかったもの（**推測で埋めない**）

実際に混ざりかけた: Plan 制約の 403 を UNKNOWN にも数えていたため、
同じ事実が Exception と UNKNOWN の両方に出ていた。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import org_audit as A                                      # noqa: E402


def _repo(**kw):
    """監査結果 1 件ぶんの形。 既定は「問題なし」。"""
    d = {
        "private": True,
        "archived": False,
        "default_branch": "main",
        "properties": {"repo_type": "core", "lifecycle": "development"},
        "files": {f: True for f in A.REQUIRED_FILES},
        "protection": None,
        "required_checks": None,
        "plan_limited": [],
    }
    d.update(kw)
    return d


def _org(**kw):
    d = {"plan": "team", "properties": {}, "issue_types": [],
         "issue_fields": [], "teams": {}}
    d.update(kw)
    return d


class TheThreeKindsStaySeparateTest(unittest.TestCase):
    """**所見 / Exception / UNKNOWN を混ぜない。**"""

    def test_a_plan_limit_is_an_exception_not_a_finding(self) -> None:
        repos = {"r": _repo(plan_limited=["branch_protection"])}
        findings, exceptions = A.classify(_org(), repos, {})
        self.assertEqual(findings, [],
                         "Plan 制約を所見に混ぜています（直せないものです）")
        self.assertEqual(len(exceptions), 1)
        self.assertEqual(exceptions[0]["capability"], "branch_protection")
        self.assertIn("GitHub Pro", exceptions[0]["reason"])

    def test_a_plan_403_is_not_also_unknown(self) -> None:
        """**同じ事実を 2 度数えないこと。**

        403 は「確かめられなかった」ではなく「機能が無い」という確定事実。
        両方に出していたので、 UNKNOWN が 5 件に膨らんでいた。
        """
        gh = A.Gh()
        gh.bin = "/nonexistent"        # 実行はしない。 分岐だけを見る

        class _R:
            returncode = 1
            stdout = ""
            stderr = ("gh: Upgrade to GitHub Pro or make this repository "
                      "public to enable this feature. (HTTP 403)")

        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: _R()
        try:
            self.assertIsNone(gh.get("/x", "テスト"))
        finally:
            subprocess.run = orig
        self.assertEqual(gh.unknown, [],
                         "Plan 制約を UNKNOWN にも数えています")

    def test_another_error_is_unknown(self) -> None:
        """Plan 以外の失敗は UNKNOWN に残すこと（黙って消さない）。"""
        gh = A.Gh()
        gh.bin = "/nonexistent"

        class _R:
            returncode = 1
            stdout = ""
            stderr = 'gh: Not Found (HTTP 404)'

        import subprocess
        orig = subprocess.run
        subprocess.run = lambda *a, **k: _R()
        try:
            gh.get("/x", "Ruleset の実値")
        finally:
            subprocess.run = orig
        self.assertEqual(len(gh.unknown), 1)
        self.assertIn("Ruleset の実値", gh.unknown[0])
        self.assertIn("404", gh.unknown[0])

    def test_no_gh_is_unknown_not_ok(self) -> None:
        gh = A.Gh()
        gh.bin = None
        self.assertIsNone(gh.get("/x", "Plan"))
        self.assertIn("gh コマンドが無い", gh.unknown[0])


class ItFindsWhatMattersTest(unittest.TestCase):

    def test_a_missing_claude_md_is_critical(self) -> None:
        """**Agent の入口が無いのは Critical。**

        他の必須ファイルより重い。 無いと Agent が repo の見方を持たない。
        """
        files = {f: True for f in A.REQUIRED_FILES}
        files["CLAUDE.md"] = False
        findings, _ = A.classify(_org(), {"r": _repo(files=files)}, {})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["severity"], A.CRITICAL)
        self.assertEqual(findings[0]["code"], "missing_required_file")

    def test_a_missing_workflow_is_only_a_warning(self) -> None:
        files = {f: True for f in A.REQUIRED_FILES}
        files[".github/workflows/basic-checks.yml"] = False
        findings, _ = A.classify(_org(), {"r": _repo(files=files)}, {})
        self.assertEqual(findings[0]["severity"], A.WARNING)

    def test_no_pr_required_is_critical(self) -> None:
        repos = {"r": _repo(private=False, required_checks=["x"],
                            protection={"pr_required": False, "approvals": 0,
                                        "force_push_blocked": True,
                                        "deletion_blocked": True})}
        findings, _ = A.classify(_org(), repos, {})
        codes = [f["code"] for f in findings]
        self.assertIn("no_pr_required", codes)
        self.assertEqual(
            next(f for f in findings if f["code"] == "no_pr_required")["severity"],
            A.CRITICAL)

    def test_zero_required_checks_is_found(self) -> None:
        """**CI が動くことと、 merge を止めることは別。**

        実測で `.github` がこの状態だった（保護はあるが必須チェック 0 件）。
        """
        repos = {"r": _repo(private=False, required_checks=[],
                            protection={"pr_required": True, "approvals": 0,
                                        "force_push_blocked": True,
                                        "deletion_blocked": True})}
        findings, _ = A.classify(_org(), repos, {})
        self.assertIn("no_required_checks", [f["code"] for f in findings])

    def test_unclassified_repo_is_found(self) -> None:
        p = {"repo_type": "unclassified", "lifecycle": "development"}
        findings, _ = A.classify(_org(), {"r": _repo(properties=p)}, {})
        self.assertIn("unclassified_repo", [f["code"] for f in findings])

    def test_a_missing_property_is_found(self) -> None:
        findings, _ = A.classify(_org(), {"r": _repo(properties={})}, {})
        codes = [f["message"] for f in findings]
        self.assertTrue(any("repo_type" in m for m in codes))
        self.assertTrue(any("lifecycle" in m for m in codes))

    def test_an_archived_repo_is_skipped(self) -> None:
        """archived は棚卸し対象外。 所見で埋めない。"""
        findings, _ = A.classify(
            _org(), {"r": _repo(archived=True, files={})}, {})
        self.assertEqual(findings, [])


class ItComparesTheDocumentWithTheImplementationTest(unittest.TestCase):
    """**文章に書いた数字は、 実装が増えても変わらない。**

    実測で 5 Repository が「63 ケース」と書いていたが、 実行すると 65。
    人では気づけない種類の drift。
    """

    def test_a_stale_count_is_found(self) -> None:
        local = {"guard_cases": 65, "documented_cases": {"a": 63, "b": 65}}
        findings, _ = A.classify(_org(), {}, local)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["repo"], "a")
        self.assertEqual(findings[0]["code"], "claude_md_stale")
        self.assertIn("63", findings[0]["message"])
        self.assertIn("65", findings[0]["message"])

    def test_nothing_is_claimed_when_the_count_is_unknown(self) -> None:
        """実件数が分からないときは、 文章を疑わないこと。"""
        local = {"guard_cases": None, "documented_cases": {"a": 63}}
        findings, _ = A.classify(_org(), {}, local)
        self.assertEqual(findings, [])

    def test_a_repo_without_a_number_is_not_flagged(self) -> None:
        local = {"guard_cases": 65, "documented_cases": {"a": None}}
        findings, _ = A.classify(_org(), {}, local)
        self.assertEqual(findings, [])

    def test_the_number_is_read_from_the_text(self) -> None:
        self.assertEqual(
            A._documented_case_count("が 65 ケースで検証している"), 65)
        self.assertEqual(A._documented_case_count("63ケース"), 63)
        self.assertIsNone(A._documented_case_count("件数の記載なし"))


class ItNeverWritesTest(unittest.TestCase):
    """**監査が設定を変えないこと。**

    変えてしまうと、 監査結果そのものが信用できなくなる。
    """

    def test_only_get_is_used(self) -> None:
        src = (HERE / "org_audit.py").read_text(encoding="utf-8")
        import re
        # コメントと docstring を外してから見る
        body = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
        for bad in ('"-X", "POST"', '"-X", "PUT"', '"-X", "PATCH"',
                    '"-X", "DELETE"', '"--method"'):
            self.assertNotIn(bad, body, f"書き込みを使っています: {bad}")
        self.assertIn('"-X", "GET"', body, "GET を明示していません")

    def test_the_free_plan_is_reported_as_an_exception(self) -> None:
        _, exceptions = A.classify(_org(plan="free"), {}, {})
        self.assertTrue(any(e["capability"] == "org_rulesets"
                            for e in exceptions))

    def test_a_paid_plan_has_no_org_exception(self) -> None:
        _, exceptions = A.classify(_org(plan="team"), {}, {})
        self.assertEqual(exceptions, [])


if __name__ == "__main__":
    unittest.main()

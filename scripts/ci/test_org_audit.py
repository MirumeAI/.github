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


#: 標準の言葉で、 組も正しい値（Core と同じ）。
GOOD = {
    "domain": "visual-inspection", "repo_type": "product-core",
    "criticality": "high", "lifecycle": "active",
    "owner_team": "visual-inspection", "governance_profile": "strict",
    "data_classification": "internal", "production_impact": "direct",
}


def _props(**kw):
    """GOOD を元に一部を変える。 None を渡すとその Property を外す。"""
    d = dict(GOOD)
    for k, v in kw.items():
        if v is None:
            d.pop(k, None)
        else:
            d[k] = v
    return d


def _repo(**kw):
    """監査結果 1 件ぶんの形。 既定は「問題なし」。"""
    d = {
        "private": True,
        "archived": False,
        "default_branch": "main",
        "properties": dict(GOOD),
        "files": {f: True for f in A.REQUIRED_FILES},
        "protection": None,
        "required_checks": None,
        "plan_limited": [],
    }
    d.update(kw)
    return d


def _schema(names):
    return {n: {"required": False, "values": list(A.STANDARD.get(n) or ())}
            for n in names}


def _org(**kw):
    """既定は標準の Property がすべて定義済み。"""
    d = {"plan": "team", "properties": _schema(A.STANDARD),
         "issue_types": [], "issue_fields": [], "teams": {}}
    d.update(kw)
    return d


def _codes(findings):
    return sorted(f["code"] for f in findings)


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
                            properties=_props(data_classification="public"),
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
                            properties=_props(data_classification="public"),
                            protection={"pr_required": True, "approvals": 0,
                                        "force_push_blocked": True,
                                        "deletion_blocked": True})}
        findings, _ = A.classify(_org(), repos, {})
        self.assertIn("no_required_checks", [f["code"] for f in findings])

    def test_unclassified_repo_is_found(self) -> None:
        p = _props(repo_type="unclassified")
        findings, _ = A.classify(_org(), {"r": _repo(properties=p)}, {})
        self.assertIn("unclassified_repo", [f["code"] for f in findings])

    def test_a_missing_property_is_found(self) -> None:
        findings, _ = A.classify(_org(), {"r": _repo(properties={})}, {})
        codes = [f["message"] for f in findings]
        self.assertTrue(any("repo_type" in m for m in codes))
        self.assertTrue(any("lifecycle" in m for m in codes))

    def test_an_archived_repo_is_skipped(self) -> None:
        """archived は棚卸し対象外。 所見で埋めない。"""
        p = _props(lifecycle="archived")
        findings, _ = A.classify(
            _org(), {"r": _repo(archived=True, files={}, properties=p)}, {})
        self.assertEqual(findings, [])

    def test_an_archived_repo_still_marked_active_is_found(self) -> None:
        """**一覧では稼働中に見える。**

        この判定は前からあったが、 archived を先に飛ばしていたので
        一度も動いていなかった。
        """
        for lc in ("active", "maintenance"):
            with self.subTest(lifecycle=lc):
                findings, _ = A.classify(_org(), {"r": _repo(
                    archived=True, properties=_props(lifecycle=lc))}, {})
                self.assertEqual(_codes(findings), ["archive_drift"])


class ItSpeaksTheStandardVocabularyTest(unittest.TestCase):
    """**Custom Properties は標準（§6）の言葉で書く。**

    2026-09-30 に寄せ終えた。 決めた値の表で所見 0 件になること、
    旧い言葉がもう通らないことを確かめる。
    """

    #: 決めた Target（as_is_inventory.md §9.1）。 名前は伏せている
    #: （このリポジトリは Public）。
    TARGET = {
        "core-a": (True, "visual-inspection", "product-core", "high",
                   "visual-inspection", "strict", "internal", "direct"),
        "core-b": (True, "visual-inspection", "product-core", "high",
                   "visual-inspection", "strict", "internal", "direct"),
        "customer-a": (True, "visual-inspection", "customer-project", "high",
                       "delivery", "strict", "customer-confidential",
                       "direct"),
        "customer-b": (True, "visual-inspection", "customer-project", "high",
                       "delivery", "strict", "customer-confidential",
                       "direct"),
        "platform": (False, "platform", "platform", "medium", "platform",
                     "strict", "public", "indirect"),
        "docs": (True, "internal", "documentation", "low", "platform",
                 "standard", "internal", "none"),
    }

    #: 寄せる前に使っていた言葉（2026-09-30 まで）。
    OLD_WORDS = {
        "repo_type": ("core", "customer", "docs", "org-config",
                      "template", "workflow"),
        "lifecycle": ("development", "production"),
    }

    def test_all_eight_properties_of_the_standard_are_audited(self) -> None:
        self.assertEqual(set(A.STANDARD), {
            "domain", "repo_type", "criticality", "lifecycle", "owner_team",
            "governance_profile", "data_classification",
            "production_impact"})

    def test_the_decided_target_has_no_findings(self) -> None:
        """**決めた値を入れたら、 監査の所見が 0 件になること。**

        適用後の確認はこれで行う。 目で表と見比べると見落とす。
        """
        keys = ("domain", "repo_type", "criticality", "owner_team",
                "governance_profile", "data_classification",
                "production_impact")
        repos = {}
        for name, (private, *vals) in self.TARGET.items():
            p = dict(zip(keys, vals), lifecycle="active")
            repos[name] = _repo(private=private, properties=p)
        findings, _ = A.classify(_org(), repos, {})
        self.assertEqual(findings, [])

    def test_the_old_words_are_no_longer_accepted(self) -> None:
        """**寄せ終えたので、 旧い言葉は「標準に無い値」になる。**

        定義からも外したので入れられないはずだが、 選択肢を戻されたときに
        気づけるようにする。
        """
        for key, words in self.OLD_WORDS.items():
            for w in words:
                with self.subTest(key=key, value=w):
                    findings, _ = A.classify(_org(), {"r": _repo(
                        properties=_props(**{key: w}))}, {})
                    self.assertEqual(_codes(findings), ["unknown_value"])
                    self.assertEqual(findings[0]["severity"], A.WARNING)

    def test_a_team_name_as_owner_is_found(self) -> None:
        """**担当は機能の言葉で表す。** Team は権限の単位（D12）。

        Team の一覧は API から取るので、 Team が増えても表を直さなくてよい。
        """
        org = _org(teams={"developers": [], "maintainers": []})
        for team in ("developers", "maintainers"):
            with self.subTest(owner_team=team):
                findings, _ = A.classify(org, {"r": _repo(
                    properties=_props(owner_team=team))}, {})
                self.assertEqual(_codes(findings), ["team_as_owner"])
        findings, _ = A.classify(org, {"r": _repo(
            properties=_props(owner_team="platform"))}, {})
        self.assertEqual(findings, [], "機能の言葉まで Team 名と誤認しています")

    def test_owner_team_is_an_open_vocabulary(self) -> None:
        """標準も owner_team の選択肢を閉じていない（「...」）。"""
        for team in ("integration", "research", "ai"):
            with self.subTest(owner_team=team):
                findings, _ = A.classify(_org(), {"r": _repo(
                    properties=_props(owner_team=team))}, {})
                self.assertEqual(findings, [])

    def test_a_value_outside_the_standard_is_found(self) -> None:
        """標準に無い値（定義から外した `template` など）を見つける。"""
        findings, _ = A.classify(_org(), {"r": _repo(
            properties=_props(repo_type="template"))}, {})
        self.assertEqual(_codes(findings), ["unknown_value"])
        self.assertEqual(findings[0]["severity"], A.WARNING)

    def test_a_missing_value_is_found(self) -> None:
        findings, _ = A.classify(_org(), {"r": _repo(
            properties=_props(governance_profile=None))}, {})
        self.assertEqual(_codes(findings), ["missing_property"])
        self.assertIn("governance_profile", findings[0]["message"])

    def test_an_undefined_property_is_counted_once(self) -> None:
        """**直すものは 1 つ。** 3 件の Repository に同じ所見を並べない。"""
        names = [k for k in A.STANDARD if k != "domain"]
        repos = {n: _repo(properties=_props(domain=None))
                 for n in ("a", "b", "c")}
        findings, _ = A.classify(_org(properties=_schema(names)), repos, {})
        self.assertEqual(_codes(findings), ["missing_property_definition"])
        self.assertTrue(findings[0]["repo"].startswith("(org)"))

    def test_an_unreadable_schema_is_not_called_missing(self) -> None:
        """**取れないことを「無い」と言わない。** 前から必須の 2 つだけ見る。"""
        repos = {"r": _repo(properties={"lifecycle": "active"})}
        findings, _ = A.classify(_org(properties=None), repos, {})
        self.assertEqual(_codes(findings), ["missing_property"])
        self.assertIn("repo_type", findings[0]["message"])

    def test_a_version_in_a_property_is_found(self) -> None:
        """**版の正本は versions.yaml。** Property にも書くと 2 つになる。"""
        findings, _ = A.classify(_org(), {"r": _repo(
            properties=_props(core_version="1.2.0"))}, {})
        self.assertEqual(_codes(findings), ["second_source_of_truth"])


class ItAppliesTheProfileRulesTest(unittest.TestCase):
    """governance_profile の組（標準 §8）。 いまは Ruleset を当てられない
    （Plan の制約）ので、 **意図として正しいかをここで確かめる。**"""

    def _find(self, **kw):
        findings, _ = A.classify(
            _org(), {"r": _repo(properties=_props(**kw))}, {})
        return _codes(findings)

    def test_direct_impact_needs_strict(self) -> None:
        """顧客 Repository は標準では standard だが、 現場へ直接届く。"""
        self.assertEqual(self._find(
            repo_type="customer-project", governance_profile="standard",
            data_classification="customer-confidential"),
            ["profile_mismatch"])

    def test_the_platform_needs_strict_without_direct_impact(self) -> None:
        self.assertEqual(self._find(
            repo_type="platform", governance_profile="standard",
            production_impact="indirect"), ["profile_mismatch"])

    def test_documentation_may_be_standard(self) -> None:
        self.assertEqual(self._find(
            repo_type="documentation", governance_profile="standard",
            production_impact="none"), [])


class ItGuardsThePublicBoundaryTest(unittest.TestCase):
    """**Public への混入は取り消せない。** 分類と公開範囲の食い違いを見る。"""

    def _find(self, private, dc):
        findings, _ = A.classify(_org(), {"r": _repo(
            private=private,
            properties=_props(data_classification=dc))}, {})
        return findings

    def test_customer_data_on_a_public_repo_is_critical(self) -> None:
        for dc in A.NEVER_PUBLIC:
            with self.subTest(data_classification=dc):
                f = self._find(False, dc)
                self.assertEqual(_codes(f), ["public_classification"])
                self.assertEqual(f[0]["severity"], A.CRITICAL)

    def test_internal_on_a_public_repo_is_a_warning(self) -> None:
        f = self._find(False, "internal")
        self.assertEqual(_codes(f), ["public_classification"])
        self.assertEqual(f[0]["severity"], A.WARNING)

    def test_a_private_repo_may_hold_customer_data(self) -> None:
        self.assertEqual(self._find(True, "customer-confidential"), [])


class ItKeepsTheFormTypesUsableTest(unittest.TestCase):
    """**Form の `type:` が Organization に無いと、 黙って種別なしに戻る。**

    Form に存在しない種別を書いたときの挙動は公式文書に無い。 種別の名前を
    Organization 側で変えたり無効にしたりしても、 Form はそのまま残る。
    """

    def test_a_type_missing_in_the_org_is_found(self) -> None:
        local = {"form_types": {"experiment.yml": "Experiment",
                                "feature.yml": "Feature"}}
        findings, _ = A.classify(
            _org(issue_types=["Task", "Bug", "Feature"]), {}, local)
        self.assertEqual(_codes(findings), ["form_type_missing"])
        self.assertIn("experiment.yml", findings[0]["message"])

    def test_nothing_is_claimed_when_the_types_are_unknown(self) -> None:
        """**取れないことを「無い」と言わない。**"""
        local = {"form_types": {"experiment.yml": "Experiment"}}
        findings, _ = A.classify(_org(issue_types=None), {}, local)
        self.assertEqual(findings, [])

    def test_a_disabled_type_is_not_usable(self) -> None:
        """無効にした種別は、 一覧にあっても Form から使えない。"""
        gh = A.Gh()

        def fake(path, why):
            if path.endswith("/issue-types"):
                return [{"name": "Feature", "is_enabled": True},
                        {"name": "Experiment", "is_enabled": False}]
            return {} if path == f"/orgs/{A.ORG}" else []
        gh.get = fake
        self.assertEqual(A.audit_org(gh)["issue_types"], ["Feature"])

    def test_the_types_are_read_from_the_real_forms(self) -> None:
        got = A._form_types(HERE.parents[1])
        self.assertLessEqual({"bug.yml", "experiment.yml", "feature.yml"},
                             set(got))
        self.assertNotIn("config.yml", got, "config.yml は Form ではない")
        self.assertEqual(got["feature.yml"], "Feature")


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

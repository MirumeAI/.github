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


#: 決めた値（Core と同じ）。
GOOD = {"group": "platform"}


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


def _schema(values=A.GROUPS):
    return {A.PROPERTY: {"required": True, "values": list(values)}}


def _org(**kw):
    """既定は分類 `group` が決めた値の並びで定義済み。"""
    d = {"plan": "team", "properties": _schema(),
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

    def test_a_missing_group_is_found(self) -> None:
        findings, _ = A.classify(_org(), {"r": _repo(properties={})}, {})
        self.assertEqual(_codes(findings), ["missing_property"])
        self.assertIn("group", findings[0]["message"])

    def test_an_archived_repo_is_skipped(self) -> None:
        """archived は棚卸し対象外。 所見で埋めない。"""
        findings, _ = A.classify(
            _org(), {"r": _repo(archived=True, files={}, properties={})}, {})
        self.assertEqual(findings, [])


class ItSpeaksTheGroupVocabularyTest(unittest.TestCase):
    """**分類は `group` の 1 つで、 決めた値の並びで書く。**

    Organization の分類が前の 8 つから `group` に替わったので、 2026-11-19 に
    決まりと道具を合わせた（MirumeAI/.github#195）。 今の割り当てで所見が
    0 件になること、 決めた並びの外の値と前の 8 つの言葉が通らないことを
    確かめる。
    """

    #: 今の割り当て。 名前は伏せている（このリポジトリは Public）。
    TARGET = {
        "core-a": (True, "platform"),
        "core-b": (True, "platform"),
        "customer-a": (True, "customer"),
        "customer-b": (True, "customer"),
        "customer-c": (True, "customer"),
        "org-config": (False, "infrastructure"),
        "docs": (True, "infrastructure"),
        "docs-private": (True, "infrastructure"),
    }

    def test_the_decided_values(self) -> None:
        self.assertEqual(A.PROPERTY, "group")
        self.assertEqual(set(A.GROUPS), {
            "tools", "research", "infrastructure", "customer", "platform"})

    def test_the_current_assignment_has_no_findings(self) -> None:
        """**今の値で、 監査の所見が 0 件になること。**

        適用後の確認はこれで行う。 目で表と見比べると見落とす。
        """
        repos = {name: _repo(private=private, properties={"group": g})
                 for name, (private, g) in self.TARGET.items()}
        findings, _ = A.classify(_org(), repos, {})
        self.assertEqual(findings, [])

    def test_every_decided_value_is_accepted(self) -> None:
        for g in A.GROUPS:
            with self.subTest(group=g):
                findings, _ = A.classify(_org(), {"r": _repo(
                    properties={"group": g})}, {})
                self.assertEqual(findings, [])

    def test_a_value_outside_the_decided_list_is_found(self) -> None:
        """前の 8 つの言葉（`product-core` など）も、 決めた並びの外。"""
        for w in ("product-core", "customer-project", "documentation",
                  "Customer", ""):
            with self.subTest(value=w):
                findings, _ = A.classify(_org(), {"r": _repo(
                    properties={"group": w})}, {})
                code = "missing_property" if not w else "unknown_value"
                self.assertEqual(_codes(findings), [code])
                self.assertEqual(findings[0]["severity"], A.WARNING)

    def test_the_old_properties_alone_do_not_count(self) -> None:
        """前の 8 つの値だけがあって `group` が無ければ、 未設定と数える。"""
        findings, _ = A.classify(_org(), {"r": _repo(properties={
            "repo_type": "product-core", "lifecycle": "active"})}, {})
        self.assertEqual(_codes(findings), ["missing_property"])

    def test_an_undefined_group_is_counted_once(self) -> None:
        """**直すものは 1 つ。** 3 件の Repository に同じ所見を並べない。"""
        repos = {n: _repo(properties={}) for n in ("a", "b", "c")}
        findings, _ = A.classify(_org(properties={}), repos, {})
        self.assertEqual(_codes(findings), ["missing_property_definition"])
        self.assertTrue(findings[0]["repo"].startswith("(org)"))

    def test_the_old_definitions_do_not_stand_in_for_group(self) -> None:
        old = {k: {"required": True, "values": []}
               for k in ("repo_type", "lifecycle", "data_classification")}
        findings, _ = A.classify(_org(properties=old), {}, {})
        self.assertEqual(_codes(findings), ["missing_property_definition"])

    def test_a_drift_in_the_choices_is_found(self) -> None:
        """Organization の選べる値が、 決めた並びと違えば知らせる。"""
        for values, lack, extra in (
                ([g for g in A.GROUPS if g != "customer"], "customer", "なし"),
                ([*A.GROUPS, "archive"], "なし", "archive")):
            with self.subTest(values=values):
                findings, _ = A.classify(
                    _org(properties=_schema(values)), {}, {})
                self.assertEqual(_codes(findings), ["property_values_drift"])
                self.assertIn(f"無い: {lack}", findings[0]["message"])
                self.assertIn(f"多い: {extra}", findings[0]["message"])

    def test_an_unreadable_schema_is_not_called_missing(self) -> None:
        """**取れないことを「無い」と言わない。** 値はそれでも見る。"""
        findings, _ = A.classify(
            _org(properties=None), {"r": _repo(properties={})}, {})
        self.assertEqual(_codes(findings), ["missing_property"])
        findings, _ = A.classify(
            _org(properties=None), {"r": _repo()}, {})
        self.assertEqual(findings, [])

    def test_a_version_in_a_property_is_found(self) -> None:
        """**版の正本は versions.yaml。** Property にも書くと 2 つになる。"""
        findings, _ = A.classify(_org(), {"r": _repo(
            properties=_props(core_version="1.2.0"))}, {})
        self.assertEqual(_codes(findings), ["second_source_of_truth"])


class ItGuardsThePublicBoundaryTest(unittest.TestCase):
    """**Public への混入は取り消せない。** 分類・名前と公開範囲を照らす。"""

    def _find(self, name, private, group):
        props = {} if group is None else {"group": group}
        findings, _ = A.classify(_org(), {name: _repo(
            private=private, properties=props)}, {})
        return findings

    def test_customer_group_on_a_public_repo_is_critical(self) -> None:
        f = self._find("r", False, "customer")
        self.assertEqual(_codes(f), ["public_customer_repo"])
        self.assertEqual(f[0]["severity"], A.CRITICAL)

    def test_a_public_customer_repo_is_critical_whatever_its_group(self) -> None:
        """分類を付け忘れた・違う値の顧客の Repository も、 名前で見つける。"""
        for group in ("customer", "infrastructure", None, "unknown"):
            with self.subTest(group=group):
                f = self._find("customer-a", False, group)
                crit = [x for x in f if x["severity"] == A.CRITICAL]
                self.assertEqual(_codes(crit), ["public_customer_repo"])
        f = self._find("customer-a", False, "customer")
        self.assertEqual(len(f), 1, "同じ Repository に Critical を 2 つ並べない")
        self.assertIn("customer-", f[0]["message"])
        self.assertIn("group=customer", f[0]["message"])

    def test_a_customer_repo_needs_the_customer_group(self) -> None:
        f = self._find("customer-a", True, "platform")
        self.assertEqual(_codes(f), ["customer_repo_group"])
        self.assertEqual(f[0]["severity"], A.WARNING)

    def test_other_public_repos_are_fine(self) -> None:
        for name, group in (("org-config", "infrastructure"),
                            ("r", "platform"), ("my-customer-a", "tools")):
            with self.subTest(name=name):
                self.assertEqual(self._find(name, False, group), [])

    def test_a_private_repo_may_hold_customer_data(self) -> None:
        self.assertEqual(self._find("customer-a", True, "customer"), [])
        self.assertEqual(self._find("r", True, "customer"), [])


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

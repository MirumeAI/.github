#!/usr/bin/env python3
"""Organization の設定差分（Governance Drift）を読み取りで監査する。

**設定は一切変更しない。** `gh api` の GET だけを使う。

なぜ機械で見るのか。手作業の監査で次を見つけた。

  ・5 Repository の CLAUDE.md が hook 回帰テストを「63 ケース」と書いて
    いたが、 実行すると 65 ケース通る
  ・Public の .github は保護されているが、 必須ステータスチェックが 0 件

**文章に書いた数字は、 実装が増えても自動では変わらない。** 人が気づけない
種類の drift なので、 実行して比べる。

3 つを区別する。 混ぜると「直せるもの」が埋もれる。

  FINDING    標準と違い、 直せるもの
  EXCEPTION  Plan / 権限の制約で実施できないもの（省略ではない）
  UNKNOWN    権限不足などで確かめられないもの。 **推測で埋めない**

実行:
    python3 scripts/ci/org_audit.py                 # 人が読む形
    python3 scripts/ci/org_audit.py --json          # 機械が読む形
    python3 scripts/ci/org_audit.py --exit-code     # Critical があれば 1

このリポジトリは **Public**。 顧客名・社内 URL・IP アドレスをここに書かない。
値はすべて実行時に API から取る。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ORG = "MirumeAI"

#: 必須ファイル。 **無いと Agent と人の入口が変わる。**
REQUIRED_FILES = (
    "CLAUDE.md",
    ".claude/settings.json",
    ".github/workflows/basic-checks.yml",
)

#: 標準（v1.2 §6）の Custom Properties と、 取りうる値。 **決めた Target を
#: ここ 1 箇所に書く。** 監査はこれとの差を所見にする。
#:
#: 標準から変えたところ（development-docs の as_is_inventory.md §9.6 で決定）:
#:   repo_type            `unclassified` を残す。 分類を忘れた新しい
#:                        Repository を見つける目印（既定値）
#:   data_classification  `public` を足す。 Public な Repository を表す値が
#:                        標準に無かった
#: `owner_team` は標準でも選択肢を閉じていない（「...」）ので、 値の種類は
#: 見ない（None）。 ただし Team の名前は使わない（担当は機能の言葉で表す。
#: Team は権限の単位）。
#: lifecycle に development / production は無い。 開発中か稼働中かは
#: production_impact が持つ（2026-09-30 に寄せた）。
STANDARD = {
    "domain": ("visual-inspection", "robotics", "platform", "research",
               "internal", "other"),
    "repo_type": ("product-core", "application", "customer-project",
                  "platform", "documentation", "delivery-control", "research",
                  "unclassified"),
    "criticality": ("high", "medium", "low"),
    "lifecycle": ("active", "maintenance", "archived"),
    "owner_team": None,
    "governance_profile": ("strict", "standard", "research"),
    "data_classification": ("public", "internal", "customer-confidential",
                            "restricted"),
    "production_impact": ("direct", "indirect", "none"),
}

#: 定義が取れなくても Repository ごとに確かめる Property（前から必須）。
ALWAYS_REQUIRED = ("repo_type", "lifecycle")

#: 版を持つ Property。 **値を入れない。** Core の版の正本は customer
#: Repository の versions.yaml（commit SHA）。 Property にも書くと正本が
#: 2 つになり、 片方だけ更新されて食い違う。
VERSION_PROPERTIES = ("core_version",)

#: governance_profile を strict にするもの（標準 §8 の「主対象」）。
#: production_impact=direct も strict（顧客 Repository が該当する）。
STRICT_TYPES = ("product-core", "platform")

#: Public な Repository に付いてはいけない分類。 付いていれば、 顧客の値が
#: 取り消せない形で外へ出ている可能性がある。
NEVER_PUBLIC = ("customer-confidential", "restricted")

#: Plan で使えない機能。 **未実施と区別する。**
PLAN_LIMITED = {
    "branch_protection": "Private Repository の branch protection は "
                         "GitHub Pro 以上で利用できる",
    "rulesets": "Private Repository の ruleset は GitHub Pro 以上で利用できる",
}

CRITICAL, WARNING, INFO = "Critical", "Warning", "Info"


class Gh:
    """`gh api` の GET だけを行う薄い包み。

    **書き込み系は呼べないようにする。** 監査が設定を変えてしまうと、
    監査結果が信用できなくなる。
    """

    def __init__(self) -> None:
        self.bin = shutil.which("gh")
        self.unknown: list[str] = []

    @property
    def available(self) -> bool:
        return self.bin is not None

    #: Plan で使えない機能を叩いたときの応答。 **UNKNOWN にしない。**
    #: これは「確かめられなかった」ではなく「機能が無い」という確定事実で、
    #: Exception として 1 度だけ数える。 両方に出すと二重計上になる。
    _PLAN_MSG = "Upgrade to GitHub Pro"

    def get(self, path: str, why: str):
        """`GET path` の結果。 取れなければ None を返し、 理由を残す。"""
        if not self.available:
            self.unknown.append(f"{why}: gh コマンドが無い")
            return None
        r = subprocess.run([self.bin, "api", "-X", "GET", path],
                           capture_output=True, text=True)
        if r.returncode != 0:
            msg = (r.stderr or "").strip().splitlines()
            reason = msg[-1] if msg else f"exit {r.returncode}"
            if self._PLAN_MSG not in reason:
                self.unknown.append(f"{why}: {reason[:160]}")
            return None
        try:
            return json.loads(r.stdout)
        except ValueError:
            self.unknown.append(f"{why}: 応答が JSON ではない")
            return None


def _guard_case_count(repo_root: Path) -> "int | None":
    """`test_guard_main.sh` が実際に通す件数。

    **主張ではなく実行で数える。** CLAUDE.md の文章と比べるため。
    """
    script = repo_root / "scripts" / "ci" / "test_guard_main.sh"
    if not script.is_file():
        return None
    r = subprocess.run(["bash", str(script)], capture_output=True, text=True,
                       cwd=str(repo_root))
    m = re.search(r"合格\s+(\d+)", r.stdout + r.stderr)
    return int(m.group(1)) if m else None


def _documented_case_count(text: str) -> "int | None":
    """CLAUDE.md が書いている件数。"""
    m = re.search(r"(\d+)\s*ケース", text)
    return int(m.group(1)) if m else None


def _form_types(repo_root: Path) -> dict:
    """Issue Form ごとの `type:`（無ければ None）。 `config.yml` は Form ではない。"""
    out = {}
    for f in sorted((repo_root / ".github" / "ISSUE_TEMPLATE").glob("*.yml")):
        if f.name == "config.yml":
            continue
        m = re.search(r"^type:\s*\"?([^\"\n]+?)\"?\s*$",
                      f.read_text(encoding="utf-8"), re.M)
        out[f.name] = m.group(1) if m else None
    return out


def audit_org(gh: Gh) -> dict:
    """Organization 側の実測値。"""
    org = gh.get(f"/orgs/{ORG}", "Organization の Plan") or {}
    out = {
        "plan": (org.get("plan") or {}).get("name"),
        # None は「取れなかった」。 {} や []（定義が無い）と区別する
        "properties": None,
        "issue_types": None,         # 有効なものだけ
        "issue_fields": None,
        "teams": {},
    }
    schema = gh.get(f"/orgs/{ORG}/properties/schema", "Custom Properties の定義")
    if schema is not None:
        out["properties"] = {
            p["property_name"]: {
                "required": p.get("required", False),
                "values": p.get("allowed_values") or [],
            }
            for p in schema
        }
    for key, path, why in (
            ("issue_types", f"/orgs/{ORG}/issue-types", "Issue Types"),
            ("issue_fields", f"/orgs/{ORG}/issue-fields", "Issue Fields")):
        got = gh.get(path, why)
        if got is not None:
            # 無効にした種別は Form から指定しても使えない
            out[key] = [x["name"] for x in got if x.get("is_enabled", True)]
    teams = gh.get(f"/orgs/{ORG}/teams", "Team 一覧")
    if teams is not None:
        for t in teams:
            repos = gh.get(f"/orgs/{ORG}/teams/{t['slug']}/repos",
                           f"Team {t['slug']} の権限")
            out["teams"][t["slug"]] = sorted(
                r["name"] for r in (repos or []))
    return out


def audit_repos(gh: Gh) -> dict:
    """Repository ごとの実測値。"""
    repos = gh.get(f"/orgs/{ORG}/repos?per_page=100", "Repository 一覧") or []
    values = gh.get(f"/orgs/{ORG}/properties/values",
                    "Custom Properties の実値") or []
    props = {v["repository_name"]:
             {p["property_name"]: p["value"] for p in v.get("properties", [])}
             for v in values}
    out = {}
    for r in repos:
        name = r["name"]
        info = {
            "private": r.get("private", True),
            "archived": r.get("archived", False),
            "default_branch": r.get("default_branch"),
            "properties": props.get(name, {}),
            "files": {},
            "protection": None,
            "required_checks": None,
            "plan_limited": [],
        }
        for f in REQUIRED_FILES:
            got = gh.get(f"/repos/{ORG}/{name}/contents/{f}",
                         f"{name} の {f}")
            info["files"][f] = got is not None
        prot = gh.get(f"/repos/{ORG}/{name}/branches/"
                      f"{info['default_branch']}/protection",
                      f"{name} の branch protection")
        if prot is None:
            # **Plan 制約と「設定していない」を区別する。**
            if info["private"]:
                info["plan_limited"].append("branch_protection")
        else:
            info["protection"] = {
                "pr_required": prot.get("required_pull_request_reviews")
                is not None,
                "approvals": ((prot.get("required_pull_request_reviews") or {})
                              .get("required_approving_review_count")),
                "force_push_blocked":
                    not (prot.get("allow_force_pushes") or {}).get("enabled"),
                "deletion_blocked":
                    not (prot.get("allow_deletions") or {}).get("enabled"),
            }
            info["required_checks"] = ((prot.get("required_status_checks")
                                        or {}).get("contexts") or [])
        out[name] = info
    return out


def _expected_properties(org: dict) -> tuple:
    """Repository ごとに値を確かめる Property。

    **Organization に定義が無いものは Repository ごとに数えない。** 定義が
    無いことを 1 度だけ数える（6 件に同じ所見を並べると、 直すものが
    1 つだと読み取れない）。 定義が取れなかったときは、 前から必須の
    ものだけを見る（取れないことを「無い」と言わない）。
    """
    defined = org.get("properties")
    if defined is None:
        return ALWAYS_REQUIRED
    return tuple(k for k in STANDARD if k in defined or k in ALWAYS_REQUIRED)


def _check_properties(name: str, info: dict, expected: tuple, teams: set,
                      add) -> dict:
    """値を 1 つずつ確かめ、 正しい値だけを返す（組の判定に使う）。"""
    p = info["properties"]
    now = {}
    for key in expected:
        v = p.get(key)
        if not v:
            add(WARNING, "missing_property", name, f"{key} が未設定")
            continue
        allowed = STANDARD[key]
        if allowed is not None and v not in allowed:
            add(WARNING, "unknown_value", name, f"{key}={v} は標準に無い値")
            continue
        if key == "owner_team" and v in teams:
            add(WARNING, "team_as_owner", name,
                f"owner_team={v} は Team の名前"
                "（担当は機能の言葉で表す。 Team は権限の単位）")
            continue
        now[key] = v
    for key in VERSION_PROPERTIES:
        if p.get(key):
            add(WARNING, "second_source_of_truth", name,
                f"{key}={p[key]}。 版の正本は versions.yaml"
                "（2 つにすると片方だけ更新されて食い違う）")
    return now


def _check_combinations(name: str, info: dict, now: dict, add) -> None:
    """値どうしの組。 標準に無い値は使わない（先に所見にしている）。"""
    rt, gp = now.get("repo_type"), now.get("governance_profile")
    if rt == "unclassified":
        add(WARNING, "unclassified_repo", name,
            "repo_type が unclassified のまま（暫定値を放置しない）")
    if gp and gp != "strict":
        why = (f"repo_type={rt}" if rt in STRICT_TYPES else
               "production_impact=direct"
               if now.get("production_impact") == "direct" else None)
        if why:
            add(WARNING, "profile_mismatch", name,
                f"{why} なのに governance_profile={gp}（標準 §8 は strict）")
    dc = now.get("data_classification")
    if not info["private"] and dc and dc != "public":
        if dc in NEVER_PUBLIC:
            add(CRITICAL, "public_classification", name,
                f"Public なのに data_classification={dc}"
                "（顧客の値が外へ出ている可能性がある）")
        else:
            add(WARNING, "public_classification", name,
                f"Public なのに data_classification={dc}"
                "（Public に置けない値を置いてよいことになっている）")


def classify(org: dict, repos: dict, local: dict) -> tuple:
    """所見を組み立てる。 `(findings, exceptions)`。"""
    findings, exceptions = [], []

    def add(sev, code, repo, msg):
        findings.append({"severity": sev, "code": code,
                         "repo": repo, "message": msg})

    expected = _expected_properties(org)
    teams = set(org.get("teams") or {})
    for name, info in repos.items():
        if info["archived"]:
            # 棚卸し対象外。 ただし lifecycle が archived でなければ、
            # 一覧では稼働中に見えるので知らせる
            if info["properties"].get("lifecycle") != "archived":
                add(WARNING, "archive_drift", name,
                    "GitHub では archived だが lifecycle が archived でない")
            continue
        # --- 必須ファイル ---
        for f, present in info["files"].items():
            if not present:
                add(CRITICAL if f == "CLAUDE.md" else WARNING,
                    "missing_required_file", name, f"{f} が無い")
        # --- Custom Properties ---
        now = _check_properties(name, info, expected, teams, add)
        _check_combinations(name, info, now, add)
        # --- 保護 ---
        for cap in info["plan_limited"]:
            exceptions.append({"repo": name, "capability": cap,
                               "reason": PLAN_LIMITED[cap]})
        if info["protection"] is not None:
            if not info["protection"]["pr_required"]:
                add(CRITICAL, "no_pr_required", name,
                    "default branch が Pull Request を必須にしていない")
            if info["required_checks"] == []:
                add(WARNING, "no_required_checks", name,
                    "必須ステータスチェックが 0 件"
                    "（CI は動くが merge を止めない）")

    # --- CLAUDE.md の文章と実装のずれ ---
    actual = local.get("guard_cases")
    if actual is not None:
        for name, documented in (local.get("documented_cases") or {}).items():
            if documented is not None and documented != actual:
                add(WARNING, "claude_md_stale", name,
                    f"CLAUDE.md は hook 回帰テストを {documented} ケースと"
                    f"書いているが、 実行すると {actual} ケース")

    # --- Issue Form の種別 ---
    # **Form に存在しない種別を書いたときの挙動は公式文書に無い。** 種別の
    # 名前を Organization 側で変えたり無効にしたりしても、 Form は黙って
    # そのまま残る。 取れなかったとき（None）は「無い」と言わない。
    types = org.get("issue_types")
    if types is not None:
        for form, t in (local.get("form_types") or {}).items():
            if t and t not in types:
                add(WARNING, "form_type_missing", f"(org) {ORG}",
                    f"Issue Form {form} の type: {t} が Organization の"
                    "有効な種別に無い（その Form の Issue に種別が付かない"
                    "可能性がある）")

    # --- Organization ---
    defined = org.get("properties")
    if defined is not None:
        for key in STANDARD:
            if key not in defined:
                add(WARNING, "missing_property_definition", f"(org) {ORG}",
                    f"標準の Property {key} が定義されていない")
    if org.get("plan") in ("free",):
        exceptions.append({
            "repo": f"(org) {ORG}", "capability": "org_rulesets",
            "reason": "Plan が free のため Private Repository への "
                      "Ruleset / branch protection が使えない"})
    return findings, exceptions


def collect_local(repo_root: Path, repos: dict, gh: Gh) -> dict:
    """手元で実行して分かること + 各 Repository の CLAUDE.md の記述。"""
    out = {"guard_cases": _guard_case_count(repo_root),
           "documented_cases": {},
           "form_types": _form_types(repo_root)}
    if out["guard_cases"] is None:
        gh.unknown.append("hook 回帰テストの実件数: "
                          "scripts/ci/test_guard_main.sh が無い")
        return out
    for name, info in repos.items():
        if not info["files"].get("CLAUDE.md"):
            continue
        got = gh.get(f"/repos/{ORG}/{name}/contents/CLAUDE.md"
                     "?ref=" + (info["default_branch"] or "main"),
                     f"{name} の CLAUDE.md 本文")
        if not got or "content" not in got:
            continue
        import base64
        try:
            text = base64.b64decode(got["content"]).decode("utf-8", "replace")
        except Exception:                                   # noqa: BLE001
            continue
        out["documented_cases"][name] = _documented_case_count(text)
    return out


def render(org, repos, findings, exceptions, unknown) -> int:
    """人が読む形。 戻り値は Critical の件数。"""
    print(f"Organization: {ORG}  Plan: {org.get('plan') or '不明'}  "
          f"Repository: {len(repos)} 件")
    props = org.get("properties")
    print("Custom Properties: "
          + ("不明" if props is None else (", ".join(props) or "なし")))
    for key, label in (("issue_types", "Issue Types（有効）"),
                       ("issue_fields", "Issue Fields")):
        got = org.get(key)
        print(f"{label}: "
              + ("不明" if got is None else (", ".join(got) or "なし")))
    for slug, names in (org.get("teams") or {}).items():
        print(f"Team {slug}: {len(names)} Repository")
    print()

    order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    n_crit = sum(1 for f in findings if f["severity"] == CRITICAL)
    if findings:
        print(f"所見 {len(findings)} 件（Critical {n_crit}）")
        for f in sorted(findings, key=lambda x: (order[x["severity"]],
                                                 x["repo"])):
            print(f"  [{f['severity']:8}] {f['repo']:32} {f['message']}")
    else:
        print("所見: なし")
    print()

    if exceptions:
        print(f"Exception {len(exceptions)} 件"
              "（Plan / 権限の制約。 **省略ではない**）")
        seen = set()
        for e in exceptions:
            key = (e["capability"], e["reason"])
            if key in seen:
                continue
            seen.add(key)
            n = sum(1 for x in exceptions if (x["capability"], x["reason"])
                    == key)
            print(f"  {e['capability']:20} {n} 件  {e['reason']}")
    print()

    if unknown:
        print(f"UNKNOWN {len(unknown)} 件（確かめられなかった。 推測しない）")
        for u in unknown[:20]:
            print(f"  {u}")
        if len(unknown) > 20:
            print(f"  ... 他 {len(unknown) - 20} 件")
    return n_crit


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="機械が読む形で出す")
    ap.add_argument("--exit-code", action="store_true",
                    help="Critical があれば 1 を返す")
    a = ap.parse_args()

    gh = Gh()
    if not gh.available:
        print("gh コマンドが無いので監査できません。"
              " 01_getting_started/claude_code_setup.md を参照してください。",
              file=sys.stderr)
        return 0 if not a.exit_code else 1

    repo_root = Path(__file__).resolve().parents[2]
    org = audit_org(gh)
    repos = audit_repos(gh)
    local = collect_local(repo_root, repos, gh)
    findings, exceptions = classify(org, repos, local)

    if a.json:
        print(json.dumps({"organization": org, "repositories": repos,
                          "findings": findings, "exceptions": exceptions,
                          "unknown": gh.unknown},
                         ensure_ascii=False, indent=2))
        n_crit = sum(1 for f in findings if f["severity"] == CRITICAL)
    else:
        n_crit = render(org, repos, findings, exceptions, gh.unknown)

    return 1 if (a.exit_code and n_crit) else 0


if __name__ == "__main__":
    sys.exit(main())

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

#: repo_type ごとに期待する lifecycle。 違っていれば棚卸し漏れの可能性。
#: 標準の Target ではなく、 **現行の運用（repository_management.md）**に合わせる。
LIFECYCLE_BY_TYPE = {
    "org-config": ("production", "maintenance"),
    "docs": ("production", "maintenance"),
    "core": ("development", "production", "maintenance"),
    "customer": ("development", "production", "maintenance"),
}

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


def audit_org(gh: Gh) -> dict:
    """Organization 側の実測値。"""
    org = gh.get(f"/orgs/{ORG}", "Organization の Plan") or {}
    out = {
        "plan": (org.get("plan") or {}).get("name"),
        "properties": {},
        "issue_types": [],
        "issue_fields": [],
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
            out[key] = [x["name"] for x in got]
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


def classify(org: dict, repos: dict, local: dict) -> tuple:
    """所見を組み立てる。 `(findings, exceptions)`。"""
    findings, exceptions = [], []

    def add(sev, code, repo, msg):
        findings.append({"severity": sev, "code": code,
                         "repo": repo, "message": msg})

    for name, info in repos.items():
        if info["archived"]:
            continue
        # --- 必須ファイル ---
        for f, present in info["files"].items():
            if not present:
                add(CRITICAL if f == "CLAUDE.md" else WARNING,
                    "missing_required_file", name, f"{f} が無い")
        # --- Custom Properties ---
        p = info["properties"]
        for key in ("repo_type", "lifecycle"):
            if not p.get(key):
                add(WARNING, "missing_property", name, f"{key} が未設定")
        rt, lc = p.get("repo_type"), p.get("lifecycle")
        if rt == "unclassified":
            add(WARNING, "unclassified_repo", name,
                "repo_type が unclassified のまま（暫定値を放置しない）")
        if rt in LIFECYCLE_BY_TYPE and lc and lc not in LIFECYCLE_BY_TYPE[rt]:
            add(INFO, "lifecycle_mismatch", name,
                f"repo_type={rt} に対して lifecycle={lc}")
        if info["archived"] and lc == "active":
            add(WARNING, "archive_drift", name,
                "GitHub では archived だが lifecycle が archived でない")
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

    # --- Organization ---
    if org.get("plan") in ("free",):
        exceptions.append({
            "repo": f"(org) {ORG}", "capability": "org_rulesets",
            "reason": "Plan が free のため Private Repository への "
                      "Ruleset / branch protection が使えない"})
    return findings, exceptions


def collect_local(repo_root: Path, repos: dict, gh: Gh) -> dict:
    """手元で実行して分かること + 各 Repository の CLAUDE.md の記述。"""
    out = {"guard_cases": _guard_case_count(repo_root),
           "documented_cases": {}}
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
    print(f"Custom Properties: {', '.join(org['properties']) or '不明'}")
    print(f"Issue Types: {', '.join(org['issue_types']) or '不明'}")
    print(f"Issue Fields: {', '.join(org['issue_fields']) or '不明'}")
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

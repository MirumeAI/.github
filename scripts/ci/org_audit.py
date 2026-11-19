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

#: 分類（Custom Properties）は `group` の 1 つ。 **決めた値の並びをここ
#: 1 箇所に書く。** 監査はこれとの差を所見にする（決まりの正本は
#: development-docs の repository_management.md。 MirumeAI/development-docs#447）。
#:
#: Organization の分類が前の 8 つ（repo_type・lifecycle・data_classification・
#: production_impact など）から `group` に替わったので、 2026-11-19 に決まりと
#: 道具を合わせた（利用者の選択 1-1・2-1。 MirumeAI/.github#195）。
#: 前の 8 つが持っていた情報は分類に持たない。 稼働しているかは顧客
#: Repository の配置の記録、 置いてよいデータは `group` で読む。
PROPERTY = "group"
GROUPS = ("tools", "research", "infrastructure", "customer", "platform")

#: 版を持つ Property。 **値を入れない。** Core の版の正本は customer
#: Repository の versions.yaml（commit SHA）。 Property にも書くと正本が
#: 2 つになり、 片方だけ更新されて食い違う。
VERSION_PROPERTIES = ("core_version",)

#: 顧客の値を置く Repository の `group`。 Public な Repository に付いて
#: いれば、 顧客の値が取り消せない形で外へ出ている可能性がある。
CUSTOMER_GROUP = "customer"

#: 顧客案件の Repository の名前の頭（`customer-<会社>-<部品>`）。 分類を
#: 付け忘れても、 名前で顧客の Repository と分かる。 大文字と小文字は区別しない。
CUSTOMER_PREFIX = "customer-"

#: Public であってよい Repository。 **ほかは Private のはず**（Core は案件の
#: 長期 Branch を、 文書は顧客 Repository の名前を持つ）。 `group` では Public で
#: よい infrastructure と Private であるべき infrastructure を分けられないので、
#: 名前で持つ（前の `data_classification=public` の代わり。 #195 の Reviewer A・B）。
PUBLIC_OK = (".github",)

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
                "default": p.get("default_value"),
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


def _check_group(name: str, info: dict, defined: "dict | None",
                 add) -> "str | None":
    """`group` の値を確かめ、 決めた並びにある値だけを返す（境の判定に使う）。

    **Organization に定義が無いときは Repository ごとに数えない。** 定義が
    無いことを 1 度だけ数える（8 件に同じ所見を並べると、 直すものが
    1 つだと読み取れない）。 定義が取れなかったときは、 値を見る（取れない
    ことを「無い」と言わない）。
    """
    p = info["properties"]
    for key in VERSION_PROPERTIES:
        if p.get(key):
            add(WARNING, "second_source_of_truth", name,
                f"{key}={p[key]}。 版の正本は versions.yaml"
                "（2 つにすると片方だけ更新されて食い違う）")
    if defined is not None and PROPERTY not in defined:
        return None
    v = p.get(PROPERTY)
    if not v:
        add(WARNING, "missing_property", name, f"{PROPERTY} が未設定")
        return None
    if v not in GROUPS:
        add(WARNING, "unknown_value", name, f"{PROPERTY}={v} は決めた値に無い")
        return None
    return v


def _check_boundary(name: str, info: dict, group: "str | None", add,
                    public_only: bool = False) -> None:
    """**Public への混入は取り消せない。** 分類・名前と公開範囲を照らす。

    名前でも見るのは、 分類を付け忘れた・取れなかった顧客の Repository も
    見落とさないため。 `public_only` は公開範囲だけを見る（archived の
    Repository。 archived でも Public なら中身は読める）。
    """
    customer_name = name.lower().startswith(CUSTOMER_PREFIX)
    if not info["private"]:
        why = [w for w, hit in ((f"{PROPERTY}={CUSTOMER_GROUP}",
                                 group == CUSTOMER_GROUP),
                                (f"名前が {CUSTOMER_PREFIX} で始まる",
                                 customer_name)) if hit]
        if why:
            add(CRITICAL, "public_customer_repo", name,
                f"Public なのに{'・'.join(why)}"
                "（顧客の値が外へ出ている可能性がある）")
        elif name not in PUBLIC_OK:
            add(WARNING, "public_unexpected", name,
                "Public であってよい Repository の並びに無いのに Public"
                "（Private のはずの Repository が公開された可能性がある）")
    if public_only:
        return
    if customer_name and group is not None and group != CUSTOMER_GROUP:
        add(WARNING, "customer_repo_group", name,
            f"顧客案件の Repository（名前が {CUSTOMER_PREFIX} で始まる）なのに"
            f" {PROPERTY}={group}")
    if group == CUSTOMER_GROUP and not customer_name and info["private"]:
        # 既定値が customer なので、 値を決めずに作った Repository はここに
        # 当たる（前の分類の unclassified の役目。 #199）。 Public なら上の
        # Critical が先に出る
        add(WARNING, "customer_group_name", name,
            f"{PROPERTY}={CUSTOMER_GROUP} なのに名前が {CUSTOMER_PREFIX} で始まらない"
            f"（値を決めずに作ると既定の {CUSTOMER_GROUP} になる。 用途に合う値を"
            " 入れるか、 顧客案件の Repository なら名前を合わせる）")


def classify(org: dict, repos: dict, local: dict) -> tuple:
    """所見を組み立てる。 `(findings, exceptions)`。"""
    findings, exceptions = [], []

    def add(sev, code, repo, msg):
        findings.append({"severity": sev, "code": code,
                         "repo": repo, "message": msg})

    defined = org.get("properties")
    for name, info in repos.items():
        if info["archived"]:
            # 棚卸し対象外（GitHub の archived だけで分かる）。 ただし
            # Public の境は見る（archived でも Public なら中身は読める）
            _check_boundary(name, info, info["properties"].get(PROPERTY), add,
                            public_only=True)
            continue
        # --- 必須ファイル ---
        for f, present in info["files"].items():
            if not present:
                add(CRITICAL if f == "CLAUDE.md" else WARNING,
                    "missing_required_file", name, f"{f} が無い")
        # --- Custom Properties と Public の境 ---
        group = _check_group(name, info, defined, add)
        _check_boundary(name, info, group, add)
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
    if defined is not None:
        if PROPERTY not in defined:
            add(WARNING, "missing_property_definition", f"(org) {ORG}",
                f"分類 {PROPERTY} が定義されていない")
        else:
            d = defined[PROPERTY]
            if not d.get("required") or d.get("default") != CUSTOMER_GROUP:
                add(WARNING, "property_default_drift", f"(org) {ORG}",
                    f"分類 {PROPERTY} が必須で既定 {CUSTOMER_GROUP} でない"
                    f"（必須: {'はい' if d.get('required') else 'いいえ'}・"
                    f"既定: {d.get('default') or 'なし'}。 値を決めずに作った"
                    " Repository を顧客の Repository と同じ厳しさで照らせない）")
            got = set(d.get("values") or [])
            lack = [v for v in GROUPS if v not in got]
            extra = sorted(got - set(GROUPS))
            if lack or extra:
                add(WARNING, "property_values_drift", f"(org) {ORG}",
                    f"分類 {PROPERTY} の選べる値が決めた並びと違う"
                    f"（無い: {', '.join(lack) or 'なし'}・"
                    f"多い: {', '.join(extra) or 'なし'}）")
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
              " development-docs の 01_getting_started/getting_started.md（準備）の §2 を参照してください。",
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

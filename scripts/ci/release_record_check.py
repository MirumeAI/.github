#!/usr/bin/env python3
"""Release と Deployment の記録が形式どおりか確かめる。

「版が存在する」ことと「この機体へ配置した」ことは別の事実なので、
別の記録として残す（形式の正本は `MirumeAI/development-docs` の
`03_customer_development/release_and_deployment.md`）。

    release/<System Release ID>.yaml     現場へ投入できる組合せ
    deployment/<site>-<line>-<日付>.yaml 配置した事実

**項目の有無だけでなく、 記録どうしの食い違いを見る。** どれも「書いて
あるのに使えない」形の壊れ方で、 必要になった瞬間まで気づけない。

    deployed_release  に書いた版が release/ に無い
    rollback_release  に書いた版が無い（**戻せない**）
    rollback_release  が deployed_release と同じ（戻す先が今の版）
    release_id        がファイル名と違う（どちらが正しいか分からない）

どちらのフォルダも無い Repository では何もしない（Core や platform）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 知っている形式の版。 **知らない値は黙って受け付けない。**
#: 項目を増やすときは、 ここと正本の文書を同じ Change で直す。
SCHEMA_VERSIONS = (1,)

#: `release/<id>.yaml` の必須項目。 `節: (鍵, ...)`
RELEASE_FIELDS = {
    None: ("schema_version", "release_id"),
    # Core の版。 **versions.yaml の内容は写さず参照する**
    #   （写すと二重管理になり、 片方を直しても効かない）。
    "software": ("versions_ref", "versions_commit"),
    "inspection": ("version", "model_sha256", "config_commit", "calibration"),
    "dataset": ("training", "evaluation", "manifest_sha256"),
    "runtime": ("os", "python"),
    "hardware": ("cameras",),
    "validation": ("report", "acceptance", "smoke_test"),
}

#: `deployment/<name>.yaml` の必須項目。
DEPLOYMENT_FIELDS = {
    None: ("schema_version", "deployed_at", "deployed_release",
           "rollback_release"),
    "target": ("site", "line"),
    "smoke_test": ("result", "checked"),
    "verified": ("release_hash_matches_manifest", "operator"),
}

SMOKE_RESULTS = ("PASS", "FAIL")


def _records(d: Path) -> list:
    """記録ファイル。 **README.md は記録ではない。**"""
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix in (".yaml", ".yml"))


def _load(p: Path, errors: list, yaml):
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        errors.append(f"{p.name}: 読めません（{type(e).__name__}）")
        return None
    if not isinstance(d, dict):
        errors.append(f"{p.name}: 中身が辞書ではありません")
        return None
    return d


def _check_fields(name: str, data: dict, spec: dict, errors: list) -> None:
    for section, keys in spec.items():
        if section is None:
            for k in keys:
                if data.get(k) in (None, ""):
                    errors.append(f"{name}: {k} がありません")
            continue
        node = data.get(section)
        if not isinstance(node, dict):
            errors.append(f"{name}: {section} の節がありません")
            continue
        for k in keys:
            if node.get(k) in (None, ""):
                errors.append(f"{name}: {section}.{k} がありません")


def _check_schema(name: str, data: dict, errors: list) -> None:
    v = data.get("schema_version")
    if v is not None and v not in SCHEMA_VERSIONS:
        errors.append(
            f"{name}: schema_version の {v!r} は知らない形式です"
            f"（知っているのは {', '.join(map(str, SCHEMA_VERSIONS))}）")


def main() -> int:
    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    rel_dir, dep_dir = root / "release", root / "deployment"
    releases, deployments = _records(rel_dir), _records(dep_dir)

    if not rel_dir.is_dir() and not dep_dir.is_dir():
        print("release/ と deployment/ が無いので何もしません。")
        return 0

    # **検査できないのに OK と言わない。**
    #   記録が無ければ何もしないが、 記録があるのに読めないときは
    #   「合格」ではなく「確かめられなかった」と返す。
    try:
        import yaml
    except ImportError:
        if not releases and not deployments:
            print("記録がまだありません。")
            return 0
        print("PyYAML が無いので記録を検査できません。"
              " `python -m pip install pyyaml` を実行してください。",
              file=sys.stderr)
        return 2

    errors: list = []
    known_ids = set()

    for p in releases:
        d = _load(p, errors, yaml)
        if d is None:
            continue
        _check_schema(p.name, d, errors)
        _check_fields(p.name, d, RELEASE_FIELDS, errors)
        rid = d.get("release_id")
        if rid:
            known_ids.add(str(rid))
            # **ファイル名と ID が違うと、 どちらが正しいか分からなくなる。**
            if str(rid) != p.stem:
                errors.append(
                    f"{p.name}: release_id が {rid!r} でファイル名と違います"
                    f"（ファイル名は {p.stem!r}）")

    for p in deployments:
        d = _load(p, errors, yaml)
        if d is None:
            continue
        _check_schema(p.name, d, errors)
        _check_fields(p.name, d, DEPLOYMENT_FIELDS, errors)

        dep, rb = d.get("deployed_release"), d.get("rollback_release")
        # **書いた版が実在すること。** 無い版を指していると、 必要になった
        #   瞬間に「戻せない」と分かる。
        for label, v in (("deployed_release", dep), ("rollback_release", rb)):
            if v and str(v) not in known_ids:
                errors.append(
                    f"{p.name}: {label} の {v!r} が release/ にありません")
        # **戻す先が今の版では、 戻せていない。**
        if dep and rb and str(dep) == str(rb):
            errors.append(
                f"{p.name}: rollback_release が deployed_release と同じです"
                "（戻す先が今の版になっています）")

        st = d.get("smoke_test")
        if isinstance(st, dict):
            r = st.get("result")
            if r is not None and str(r) not in SMOKE_RESULTS:
                errors.append(
                    f"{p.name}: smoke_test.result の {r!r} は"
                    f" {' / '.join(SMOKE_RESULTS)} のどちらかにしてください")

    print(f"検査: release {len(releases)} 件 / deployment {len(deployments)} 件")
    if errors:
        print(f"\nNG: {len(errors)} 件", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print("\n  形式は MirumeAI/development-docs の"
              " 03_customer_development/release_and_deployment.md を"
              "参照してください。", file=sys.stderr)
        return 1
    print("OK: 記録は形式どおりです")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

根の `versions.yaml`（案件が次の System Release で使う Core の組合せ。選択だけ）も
見る。schema 2 は形を確かめ、schema 1 は古い形として読むだけにする（P1-04）。

    versions.yaml     選択のほかの項目（配置・検証・移行の経緯）がある
                      tag と commit の片方だけ・形の違う値（未選択は両方 null）
                      撮像側を選ばずに PDM だけを選んでいる

根の `versions.dev.yaml`（任意。開発中に使う Core の commit）も見る（#153）。
**System Release は参照しない**ので、 開発の版は配置に流れない。

    versions.dev.yaml Core ごとの 40 桁の commit のほかの項目・形の違う値がある
    release/          software.versions_ref が versions.yaml でない

`versions.dev.yaml` の 1 ファイルだけを、 同じ決まりで確かめる入口もある（#166）。 顧客案件の
起動の道具が、 決まりを写さずに呼ぶ（この script は顧客案件へ写し、 写しの照合が正本との
ずれを止める）。 ほかの記録は見ない。

    python3 release_record_check.py --dev-pin <versions.dev.yaml の道>

    0  形どおり。 標準出力に `<Core の鍵>=<40 桁の commit>` を、 書いてある Core だけ
       （撮像側・PDM の順に）1 行ずつ出す。 起動の道具はこの値を使い、 自分で読まない
       （YAML の読み方の違いで、 確かめた値と使う値が分かれないように）
    1  形が違う・ファイルが無い（標準出力には何も出さない。 理由は標準エラー）
    2  使い方の違い・PyYAML が無い（確かめられなかった）

開発の機体だけの設定（根の `dev/`。 撮像側の `MIRUME_DEV_CONFIG` に渡す `dev/config/`）も、
System Release と配置の記録から指さない（#156）。 配る道具は `config/` だけを配るので、
指すと、 機体に無い設定を据え付けに使うことになる。

    release/・deployment/  この Repository の `dev/` の道・`./dev`・前にほかの道がある `dev/config`・
                          `MIRUME_DEV_CONFIG` を、 鍵か値に書いている（`/dev/video0` のような
                          機器の道は見ない。 出すのは欄だけで、 値と決まりの外の鍵は出さない）

Core の写しの道を `CORE_ROOT_IMAGE_ACQUISITION_PDM`・`CORE_ROOT_PDM` に渡した
ときだけ、タグが注釈つきで書いた commit を指すことと、PDM の行が撮像側のタグの
commit にある `pdm.lock.json` と同じことも照らす（CI は Private の Core を読めない
ので、手元の確かめで渡す）。

`deployment/` の schema 2（P1-05）は機体ごとの記録の並びで見る。 記録の ID は
`<機体の記号>-<通し番号 4 桁>` で、 通し番号は 1 から抜けも重なりも無く、
`supersedes` は前の記録を指す。 **今の状態は機体ごとに通し番号が一番大きい記録**で、
ファイルの更新日時では決めない。 LEGACY は `observed`、 ACTIVE は `deployed_release`・
`rollback_to`・`config_commit`・`artifacts`・`model_sha256` を持ち、 結果の欄
（`smoke_test`・`verified`）は持たない（結果は検証の記録にだけ書く）。

System Release の記録（P1-07）は、 採用したモデルの版（`<案件の記号>-M<年>.<月>.<番号>`）と、
学習用・評価用の Dataset（`DS-<案件の記号>-v<番号>`・`DS-<案件の記号>-eval-v<番号>`）の
それぞれに、 ファイルの数・大きさ・manifest の sha256 を持つ（データそのものは置き場に置き、
Git にはどのデータかだけを書く）。 案件の記号は `release_id` と同じでなければならない
（別の案件の物を混ぜない）。

`validation/` の検証の記録（P1-05）は、 項目と全体の結果の食い違い・指す先の無い
記録・`supersedes` の枝分かれを止め、 対象 × 手順の ID ごとの状態（一番新しい記録が
PASS なら VERIFIED、 FAIL なら FAILED、 PARTIAL なら NOT VERIFIED）を出す。

顧客 Repository の新しい形（根に `recipes/case.yaml` がある。 混入の検査と同じ見分け。 #173）では、
System Release に製品のパッケージ（`product_package`: 名前・版・wheel の sha256）を載せ、 使った
レシピと機体の設定の commit は `inspection.config_commit`（40 桁）にする。 製品のパッケージのある
System Release を配った `ACTIVE` の配置の記録は、 作業ルートの製品の設定とレシピのずれ
（`recipe_drift`: 比べたレシピの commit と、 製品ごとのずれた鍵の名前。 **値は書かない**）を持ち、
`artifacts` にその wheel を持つ。 前の記録と同じ System Release の `ACTIVE` の記録（ずれの確かめ）は、
配置の欄を前の記録と同じにする。

    product_package   新しい形で足す System Release に無い・前の形で書いた・形が違う
    recipe_drift      製品のパッケージのある System Release を配った ACTIVE の記録に無い・ほかの記録にある・形が違う
    artifacts         製品のパッケージの wheel（名前・版・sha256）が無い
    ずれの確かめ      rollback_to・config_commit・artifacts・model_sha256 が前の記録と違う

**ずれを残したまま、 次の System Release を作らない。** `--base-ref <基準の commit>` を渡すと、
基準に無かった `release/` の記録（この Pull Request で足した System Release）ごとに、 製品の
パッケージのある System Release を配って今 `ACTIVE` の機体のすべてで、 一番新しい記録が、 その
System Release の `inspection.config_commit` と比べたずれ無し（`products: {}`）かを照らす。 足した
後の記録を止めない（起きた配置の記録を止めない）ため、 照らすのは足した System Release だけ。
`--base-ref` が無ければ照らさず、 そう出す。 基準を読めなければ、 合格と言わず 2 を返す。

どれも無い Repository では何もしない（Core や platform）。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

#: 知っている形式の版。 **知らない値は黙って受け付けない。**
#: 項目を増やすときは、 ここと正本の文書を同じ Change で直す。
SCHEMA_VERSIONS = (1,)

#: `release/<id>.yaml` の必須項目。 `節: (鍵, ...)`
RELEASE_FIELDS = {
    None: ("schema_version", "release_id"),
    # Core の版。 **versions.yaml の内容は写さず参照する**
    #   （写すと二重管理になり、 片方を直しても効かない）。
    "software": ("versions_ref", "versions_commit"),
    "inspection": ("version", "model_sha256", "model_files", "model_bytes",
                   "config_commit", "calibration"),
    "dataset": ("training", "evaluation"),
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

#: `versions.yaml` の知っている形式。 1 は古い形として読むだけ（選択を確かめない）。
VERSIONS_SCHEMAS = (1, 2)
#: Core の節と Repository。 **選択のほかの項目は書かない**（配置は deployment/、
#: 検証は validation/、移行の経緯は Issue）。
VERSIONS_CORES = {
    "image_acquisition_pdm": "MirumeAI/Image-Acquisition-PDM",
    "pdm": "MirumeAI/PDM",
}
#: 開発中に使う Core の commit を置くファイル（任意。#153）。 **System Release は
#: versions.yaml だけを参照する**（下の `VERSIONS_REF`）ので、 ここの版は配置に流れない。
VERSIONS_DEV = "versions.dev.yaml"
#: System Release が参照してよい Core の組合せのファイル
VERSIONS_REF = "versions.yaml"
#: `versions.dev.yaml` の知っている形式
VERSIONS_DEV_SCHEMAS = (1,)
#: 開発の機体だけの設定への参照（#156）。 混入の検査の S4（`customer_isolation_check._DEV_REF`）と同じ形:
#: この Repository の `dev/` の道（`./`・`../` で始まるものも）・`./dev` そのもの・前にほかの道がある
#: `dev/config`（配置先の絶対の道。 末尾の `/` が無くても）・`MIRUME_DEV_CONFIG`。
_DEV_REF = re.compile(r"(?<![A-Za-z0-9_./-])(?:\.\.?/){0,16}dev/|(?<![A-Za-z0-9_./-])\.\.?/dev(?![A-Za-z0-9_./-])"
                      r"|(?<![A-Za-z0-9_.-])dev/config(?![A-Za-z0-9_-])"
                      r"|(?<![A-Za-z0-9_])MIRUME_DEV_CONFIG(?![A-Za-z0-9_])")
#: 手元の確かめで Core の写しの道を渡す環境の変数。
CORE_ROOT_ENV = {
    "image_acquisition_pdm": "CORE_ROOT_IMAGE_ACQUISITION_PDM",
    "pdm": "CORE_ROOT_PDM",
}
TAG_RE = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA_RE = re.compile(r"[0-9a-f]{40}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")

#: 配置の記録 schema 2 の機体の記号（中立。 IP・製造番号・装置名を書かない）と記録の ID。
MACHINE_RE = re.compile(r"[A-Z][A-Z0-9]{1,7}")
RECORD_ID_RE = re.compile(r"([A-Z][A-Z0-9]{1,7})-([0-9]{4})")
DEPLOY_STATUSES = ("LEGACY", "ACTIVE", "RETIRED")
#: 検証の記録。
VR_ID_RE = re.compile(r"VR-[A-Z0-9]+-[0-9]{4}-[0-9]{3}")
VR_SCOPES = ("product", "system", "deployment")
VR_ENVIRONMENTS = ("real-machine", "bench", "ci")
VR_RESULTS = ("PASS", "FAIL", "PARTIAL")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
#: System Release の ID と、 中のモデルの版・Dataset の ID（P1-07）。 どれも同じ案件の記号を持つ。
RELEASE_ID_RE = re.compile(r"([A-Z0-9]+)-R[0-9]{4}\.[0-9]{2}\.[0-9]+")
MODEL_VERSION_RE = re.compile(r"([A-Z0-9]+)-M[0-9]{4}\.[0-9]{2}\.[0-9]+")
DATASET_ID_RE = {"training": re.compile(r"DS-([A-Z0-9]+)-v[0-9]+"),
                 "evaluation": re.compile(r"DS-([A-Z0-9]+)-eval-v[0-9]+")}
DATASET_FIELDS = ("id", "files", "bytes", "manifest_sha256")
#: 顧客 Repository の新しい形の印（根に置く。 混入の検査と同じ見分け。 MirumeAI/development-docs#392）。
CASE_FILE = "recipes/case.yaml"
#: System Release の製品のパッケージ（新しい形。 #173）。 決まりの外の鍵は書かない。
PACKAGE_FIELDS = ("name", "version", "wheel_sha256")
#: 配布の名前（PEP 508 の名前の形）。 比べるときは PEP 503 の形（小文字・`-`）にそろえる。
PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
#: 配置の記録のずれ（#173）。 比べたレシピの commit と、 製品ごとのずれた鍵の名前（値は書かない）。
DRIFT_FIELDS = ("compared_commit", "products")
#: ずれた鍵の名前。 `.` で区切り、 各段は文字か `_` で始まる（後ろに `[番号]` を付けてよい）。
#: `=`・空白・数で始まる段を断り、 値を書いた形を通さない。
DRIFT_KEY_RE = re.compile(r"[^\W\d][\w-]*(?:\[[0-9]+\])*(?:\.[^\W\d][\w-]*(?:\[[0-9]+\])*)*")
#: 製品の名前（`recipes/products/<製品の名前>.yaml` のファイルの名前にできる形）。
_PRODUCT_BAD = re.compile(r"[/\\\x00-\x1f\x7f]|^\.|^$")
#: 基準の commit（混入の検査の `--base-ref` と同じ形。 オプションと取り違えない）。
_HEX_REF = re.compile(r"[0-9a-f]{7,40}")
#: 前の記録と同じ System Release の ACTIVE の記録（ずれの確かめ）が、 前の記録と同じにする欄。
SAME_STATE_FIELDS = ("rollback_to", "config_commit", "artifacts", "model_sha256")
#: 出力にそのまま出す欄の名前（記録の決まった鍵）。 ほかは `<*>`（記録に書いた任意の名前を出さない。 #156）。
#: 製品の名前（`recipe_drift.products` の下の鍵）は出さない。
_SHOWN_KEYS = frozenset(
    {k for spec in (RELEASE_FIELDS, DEPLOYMENT_FIELDS) for sec, keys in spec.items()
     for k in ((sec,) if sec else ()) + keys}
    | set(DATASET_FIELDS) | set(PACKAGE_FIELDS) | set(DRIFT_FIELDS)
    | {"record_id", "machine", "sequence", "supersedes", "status", "recorded_by", "role", "account",
       "observed", "deployed_release", "rollback_to", "rolled_back_from", "config_commit", "artifacts",
       "name", "version", "sha256", "model_sha256", "release_hash_matches_manifest", "operator",
       "product_package", "recipe_drift"})
#: 知らせの文で言う決まった形（**値は出さない**。 欄に道などを書いても、 出力に出さない）。
FORMS = {"release_id": "<案件の記号>-R<年>.<月 2 桁>.<番号>",
         "inspection.version": "<案件の記号>-M<年>.<月 2 桁>.<番号>",
         "dataset.training.id": "DS-<案件の記号>-v<番号>",
         "dataset.evaluation.id": "DS-<案件の記号>-eval-v<番号>"}


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


def _count(v, least: int) -> bool:
    """ファイルの数・大きさ（整数。 真偽は数として読まない）。"""
    return isinstance(v, int) and not isinstance(v, bool) and v >= least


def _sha256_ok(v) -> bool:
    """manifest の sha256（64 桁の小文字の 16 進。 数として読まれた値は、 先頭の 0 が消えうるので不可）。"""
    return isinstance(v, str) and bool(HEX64_RE.fullmatch(v))


def _same_code(name: str, label: str, value, rx, code, errors: list) -> None:
    """名前の形と、 案件の記号が System Release と同じか。"""
    if value in (None, ""):
        return                                  # 無いことは _check_fields が言う
    m = rx.fullmatch(str(value))
    if not m:
        errors.append(f"{name}: {label} が決まった形（{FORMS[label]}）ではありません")
    elif code and m.group(1) != code:
        errors.append(f"{name}: {label} の案件の記号が release_id と違います（別の案件の物を混ぜない）")


def _check_release_data(name: str, d: dict, errors: list) -> None:
    """採用したモデルと Dataset の版・数・大きさ・manifest の sha256（P1-07）。"""
    rid = d.get("release_id")
    m = RELEASE_ID_RE.fullmatch(str(rid or ""))
    if rid not in (None, "") and not m:
        # **形が違うと案件の記号を取り出せず、 下の照らしが黙って外れる。** そこで記録ごと止める
        errors.append(f"{name}: release_id が決まった形（{FORMS['release_id']}）ではありません"
                      "（案件の記号を照らせない）")
    code = m.group(1) if m else None
    ins = d.get("inspection")
    if isinstance(ins, dict):
        _same_code(name, "inspection.version", ins.get("version"), MODEL_VERSION_RE, code, errors)
        if ins.get("model_sha256") not in (None, "") and not _sha256_ok(ins.get("model_sha256")):
            errors.append(f"{name}: inspection.model_sha256 は 64 桁の小文字の 16 進を引用符で囲んで書いてください")
        for k, least in (("model_files", 1), ("model_bytes", 0)):
            if ins.get(k) not in (None, "") and not _count(ins.get(k), least):
                errors.append(f"{name}: inspection.{k} は {least} 以上の整数にしてください")
    ds = d.get("dataset")
    if not isinstance(ds, dict):
        return
    if "manifest_sha256" in ds:
        errors.append(f"{name}: dataset.manifest_sha256 は使いません（学習用・評価用のそれぞれに書く）")
    for k, rx in DATASET_ID_RE.items():
        node = ds.get(k)
        if node in (None, ""):
            continue
        if not isinstance(node, dict):
            errors.append(f"{name}: dataset.{k} は {'・'.join(DATASET_FIELDS)} を持つ形にしてください")
            continue
        for f in DATASET_FIELDS:
            if node.get(f) in (None, ""):
                errors.append(f"{name}: dataset.{k}.{f} がありません")
        _same_code(name, f"dataset.{k}.id", node.get("id"), rx, code, errors)
        for f, least in (("files", 1), ("bytes", 0)):
            if node.get(f) not in (None, "") and not _count(node.get(f), least):
                errors.append(f"{name}: dataset.{k}.{f} は {least} 以上の整数にしてください")
        if node.get("manifest_sha256") not in (None, "") and not _sha256_ok(node.get("manifest_sha256")):
            errors.append(f"{name}: dataset.{k}.manifest_sha256 は 64 桁の小文字の 16 進を引用符で囲んで書いてください")


def _package_of(release) -> "dict | None":
    """System Release の製品のパッケージ（無い・形の違う System Release は None）。"""
    pkg = release.get("product_package") if isinstance(release, dict) else None
    return pkg if isinstance(pkg, dict) else None


def _norm_name(name: str) -> str:
    """配布の名前を PEP 503 の形にそろえる（`My_Pkg` と `my-pkg` は同じ配布）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _check_product_package(name: str, d: dict, new_form: bool, errors: list) -> None:
    """System Release の製品のパッケージ（#173）。 要るかは、 足した System Release で見る（`_check_added_releases`）。"""
    if "product_package" not in d:
        return
    pkg = d.get("product_package")
    if not new_form:
        errors.append(f"{name}: product_package は、新しい形（{CASE_FILE} がある Repository）の System Release にだけ書きます")
        return
    if not isinstance(pkg, dict):
        errors.append(f"{name}: product_package は {'・'.join(PACKAGE_FIELDS)} を持つ形にしてください")
        return
    if set(pkg) - set(PACKAGE_FIELDS):
        errors.append(f"{name}: product_package に決まりの外の鍵があります（書けるのは {'・'.join(PACKAGE_FIELDS)}）")
    n, v = pkg.get("name"), pkg.get("version")
    if not (isinstance(n, str) and PACKAGE_NAME_RE.fullmatch(n)):
        errors.append(f"{name}: product_package.name は配布の名前（英数字と . _ -）にしてください")
    if not (isinstance(v, str) and v.strip() == v and v):
        errors.append(f"{name}: product_package.version は版を引用符で囲んだ文字にしてください（数として読まれると 1.10 が 1.1 になる）")
    if not _sha256_ok(pkg.get("wheel_sha256")):
        errors.append(f"{name}: product_package.wheel_sha256 は wheel の 64 桁の小文字の 16 進を引用符で囲んで書いてください")
    # 組み立て器が使うレシピと機体の設定の commit。 ずれの確かめの compared_commit と同じ形で比べる
    ins = d.get("inspection")
    cc = ins.get("config_commit") if isinstance(ins, dict) else None
    if cc not in (None, "") and not (isinstance(cc, str) and SHA_RE.fullmatch(cc)):
        errors.append(f"{name}: 製品のパッケージのある System Release の inspection.config_commit は、"
                      "レシピと機体の設定の 40 桁の commit SHA にします")


def _check_drift(name: str, v, errors: list) -> None:
    """配置の記録のずれ（#173）。 **値は書かない**ので、 鍵の名前の形だけを受ける。 製品の名前と鍵の名前は出さない。"""
    if not (isinstance(v, dict) and set(v) == set(DRIFT_FIELDS)):
        errors.append(f"{name}: recipe_drift は {'・'.join(DRIFT_FIELDS)} だけを持つ形にします（ずれが無ければ products: {{}}）")
        return
    cc = v.get("compared_commit")
    if not (isinstance(cc, str) and SHA_RE.fullmatch(cc)):
        errors.append(f"{name}: recipe_drift.compared_commit は、比べたレシピの 40 桁の commit SHA にします")
    prods = v.get("products")
    if not isinstance(prods, dict):
        errors.append(f"{name}: recipe_drift.products は、ずれた製品ごとの鍵の名前の対応にします（ずれが無ければ {{}}）")
        return
    for p, keys in prods.items():
        if not isinstance(p, str) or _PRODUCT_BAD.search(p):
            errors.append(f"{name}: recipe_drift.products の製品の名前が、ファイルの名前にできる形ではありません")
        if not (isinstance(keys, list) and keys
                and all(isinstance(k, str) and DRIFT_KEY_RE.fullmatch(k) for k in keys)
                and len(set(keys)) == len(keys)):
            errors.append(f"{name}: recipe_drift.products.<*> は、ずれた鍵の名前（例 judge.threshold。値は書かない）の"
                          "重ならない並びにします（ずれの無い製品は書かない）")


def _dev_refs(name: str, node, errors: list, at: str = "") -> None:
    """System Release と配置の記録が、 開発の機体だけの設定を指していないか（#156）。

    鍵も値も見る。 **値は出さない**（どの欄かだけ。 決まった鍵のほかは `<*>`）。 当たった鍵の
    下は見ない（鍵が出力に出ないように）。
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and _DEV_REF.search(k):
                errors.append(f"{name}: {at or '根'} の鍵が、開発の機体だけの設定"
                              "（dev/・MIRUME_DEV_CONFIG）を指しています（機体へ配る設定だけを指す）")
                continue
            shown = str(k) if k in _SHOWN_KEYS else "<*>"
            _dev_refs(name, v, errors, f"{at}.{shown}" if at else shown)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _dev_refs(name, v, errors, f"{at}[{i}]")
    elif isinstance(node, str) and _DEV_REF.search(node):
        errors.append(f"{name}: {at or '根'} が、開発の機体だけの設定"
                      "（dev/・MIRUME_DEV_CONFIG）を指しています（機体へ配る設定だけを指す）")


def _check_schema(name: str, data: dict, errors: list) -> None:
    v = data.get("schema_version")
    if v is not None and v not in SCHEMA_VERSIONS:
        errors.append(
            f"{name}: schema_version の {v!r} は知らない形式です"
            f"（知っているのは {', '.join(map(str, SCHEMA_VERSIONS))}）")


def _git(root: str, *args: str) -> "str | None":
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def _verify_cores(chosen: dict, roots: dict, errors: list) -> None:
    """Core の写しで、 タグ・commit と、 PDM の行と撮像側の lock を照らす。"""
    for key, (tag, commit) in chosen.items():
        root = roots[key]
        if _git(root, "cat-file", "-t", f"refs/tags/{tag}") != "tag":
            errors.append(f"versions.yaml: {key} の {tag} が Core の写しに注釈つきの"
                          "タグとしてありません（タグを取り直してから流してください）")
        elif _git(root, "rev-parse", f"refs/tags/{tag}^{{commit}}") != commit:
            errors.append(f"versions.yaml: {key}.commit が {tag} の指す commit と違います")
    ia = chosen.get("image_acquisition_pdm")
    if not ia:
        return
    # **組合せの正本は撮像側のタグの commit にある lock。** ここは写しなので、
    #   lock が無いタグでは組合せを確かめられない。
    text = _git(roots["image_acquisition_pdm"], "show", f"{ia[1]}:pdm.lock.json")
    if text is None:
        errors.append(f"versions.yaml: 撮像側の {ia[0]} の commit に pdm.lock.json が無く、"
                      "組合せを確かめられません")
        return
    try:
        lock = json.loads(text)["pdm"]
    except (ValueError, KeyError, TypeError):
        errors.append(f"versions.yaml: 撮像側の {ia[0]} の pdm.lock.json を読めません")
        return
    pdm = chosen.get("pdm")
    if pdm and (pdm[0] != lock.get("tag") or pdm[1] != lock.get("commit")):
        errors.append(f"versions.yaml: pdm の行が撮像側の {ia[0]} の pdm.lock.json"
                      f"（{lock.get('tag')}）と違います")


def _check_versions_dev(p: Path, errors: list, notes: list, yaml) -> "dict | None":
    """開発中の固定。 Core ごとの 40 桁の commit だけを持つ（#153）。 読んだ中身を返す（読めなければ None）。"""
    d = _load(p, errors, yaml)
    if d is None:
        return None
    if d.get("schema_version") not in VERSIONS_DEV_SCHEMAS:
        errors.append(f"{VERSIONS_DEV}: schema_version は"
                      f" {', '.join(map(str, VERSIONS_DEV_SCHEMAS))} です")
    unknown = sorted(str(k) for k in d if k not in ("schema_version", *VERSIONS_CORES))
    if unknown:
        errors.append(f"{VERSIONS_DEV}: Core の commit のほかの項目があります（{', '.join(unknown)}）")
    cores = [k for k in VERSIONS_CORES if k in d]
    if not cores:
        errors.append(f"{VERSIONS_DEV}: Core の commit がありません")
    for key in cores:
        if not (isinstance(d[key], str) and SHA_RE.fullmatch(d[key])):
            errors.append(f"{VERSIONS_DEV}: {key} は 40 桁の commit SHA にします")
    notes.append(f"{VERSIONS_DEV}: 開発中の固定（System Release と配置には使えません）")
    return d


def _check_versions(p: Path, errors: list, notes: list, yaml) -> None:
    d = _load(p, errors, yaml)
    if d is None:
        return
    v = d.get("schema_version")
    if v == 1:
        notes.append("versions.yaml: schema_version 1（古い形。 Core の選択は確かめない）")
        return
    if v not in VERSIONS_SCHEMAS:
        errors.append(f"versions.yaml: schema_version の {v!r} は知らない形式です"
                      f"（知っているのは {', '.join(map(str, VERSIONS_SCHEMAS))}）")
        return
    extra = sorted(str(k) for k in d if k not in ("schema_version", *VERSIONS_CORES))
    if extra:
        errors.append(f"versions.yaml: 選択のほかの項目があります（{', '.join(extra)}）。"
                      " 配置・検証・移行の経緯は書きません")
    chosen = {}
    for key, repo in VERSIONS_CORES.items():
        node = d.get(key)
        if not isinstance(node, dict):
            errors.append(f"versions.yaml: {key} の節がありません")
            continue
        unknown = sorted(str(k) for k in node if k not in ("repository", "tag", "commit"))
        if unknown:
            errors.append(f"versions.yaml: {key} に知らない項目があります（{', '.join(unknown)}）")
        if node.get("repository") != repo:
            errors.append(f"versions.yaml: {key}.repository は {repo} です")
        tag, commit = node.get("tag"), node.get("commit")
        if tag is None and commit is None:
            continue                                     # 未選択
        # **片方だけ・形の違う値を黙って受け付けない。**
        if not (isinstance(tag, str) and TAG_RE.fullmatch(tag)):
            errors.append(f"versions.yaml: {key}.tag は Product Release のタグ（vX.Y.Z）に"
                          "します（未選択なら tag と commit の両方を null）")
        elif not (isinstance(commit, str) and SHA_RE.fullmatch(commit)):
            errors.append(f"versions.yaml: {key}.commit は 40 桁の commit SHA にします"
                          "（未選択なら tag と commit の両方を null）")
        else:
            chosen[key] = (tag, commit)
    if "pdm" in chosen and "image_acquisition_pdm" not in chosen:
        errors.append("versions.yaml: 撮像側を選ばずに PDM だけを選べません"
                      "（PDM は撮像側のタグの pdm.lock.json に従います）")
    if not chosen:
        notes.append("versions.yaml: 未選択")
        return
    names = "・".join(f"{k} {t}" for k, (t, _) in chosen.items())
    roots = {k: os.environ.get(CORE_ROOT_ENV[k], "") for k in chosen}
    if not all(roots.values()):
        notes.append(f"versions.yaml: {names}（タグ・commit・lock は Core の写しを"
                     f" {'・'.join(CORE_ROOT_ENV[k] for k in chosen)} に渡したときだけ照らす）")
        return
    _verify_cores(chosen, roots, errors)
    notes.append(f"versions.yaml: {names}（タグ・commit・lock を Core の写しで照らした）")


def _person(name: str, d: dict, key: str, errors: list) -> None:
    v = d.get(key)
    if not (isinstance(v, dict) and v.get("role") and v.get("account")):
        errors.append(f"{name}: {key} に role と account を書きます（人）")


def _earlier(rid, machine: str, seq: int, by_id: dict) -> bool:
    """同じ機体の、 前の記録の ID か。"""
    m = RECORD_ID_RE.fullmatch(str(rid)) if rid else None
    return bool(m) and m.group(1) == machine and int(m.group(2)) < seq and rid in by_id


def _latest(by_id: dict) -> dict:
    """機体の記号 → その機体の一番新しい記録（**今の状態は、 通し番号が一番大きい記録**）。"""
    out: dict = {}
    for d in by_id.values():
        m = d.get("machine")
        if m not in out or d.get("sequence", 0) > out[m].get("sequence", 0):
            out[m] = d
    return out


def _check_deployments_v2(recs: list, known_ids: set, errors: list, notes: list,
                          releases: "dict | None" = None) -> dict:
    """配置の記録 schema 2。 機体ごとの並びを確かめ、 記録の ID → 中身を返す。

    `releases` は System Release の ID → 中身。 製品のパッケージのある System Release を配った記録の、
    ずれ（`recipe_drift`）と wheel を照らすのに使う（#173）。
    """
    releases = releases or {}
    by_id, by_machine = {}, {}
    for p, d in recs:
        name = p.name
        m, seq = d.get("machine"), d.get("sequence")
        if not (isinstance(m, str) and MACHINE_RE.fullmatch(m)):
            errors.append(f"{name}: machine は中立の機体の記号（例 M01。 英大文字で始まる英大文字と数字の 2〜8 字）にします")
            continue
        if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1):
            errors.append(f"{name}: sequence は 1 から数える整数にします")
            continue
        rid = f"{m}-{seq:04d}"
        if d.get("record_id") != rid:
            errors.append(f"{name}: record_id は {rid}（machine と sequence から決まる）にします")
            continue
        if p.stem != rid:
            errors.append(f"{name}: ファイル名は record_id と同じ {rid}.yaml にします")
        want = None if seq == 1 else f"{m}-{seq - 1:04d}"
        if d.get("supersedes") != want:
            errors.append(f"{name}: supersedes は {want or 'null（その機体の最初の記録）'} にします")
        if d.get("status") not in DEPLOY_STATUSES:
            errors.append(f"{name}: status は {' / '.join(DEPLOY_STATUSES)} のどれかにします")
        _person(name, d, "recorded_by", errors)
        for k in ("smoke_test", "verified"):
            if k in d:
                errors.append(f"{name}: schema 2 の配置の記録に {k} は書きません（結果は validation/ の検証の記録にだけ書く）")
        by_id[rid] = d
        by_machine.setdefault(m, []).append(seq)
    # **今の状態は機体ごとに sequence が一番大きい記録。** 抜けがあると、 どれが今か分からない
    for m, seqs in sorted(by_machine.items()):
        if sorted(seqs) != list(range(1, len(seqs) + 1)):
            errors.append(f"deployment/: 機体 {m} の記録の通し番号に抜けがあります（1 から {max(seqs)} までのうち {len(seqs)} 件）")
    for rid, d in sorted(by_id.items()):
        name, st, m, seq = f"{rid}.yaml", d.get("status"), d.get("machine"), d.get("sequence")
        rb = d.get("rolled_back_from")
        if rb is not None and not _earlier(rb, m, seq, by_id):
            errors.append(f"{name}: rolled_back_from は同じ機体の前の記録の ID にします")
        if st == "LEGACY":
            if not (isinstance(d.get("observed"), dict) and d.get("observed")):
                errors.append(f"{name}: LEGACY の記録には observed（棚卸しで見た状態）を書きます")
        elif st == "ACTIVE":
            rel = d.get("deployed_release")
            if not rel or str(rel) not in known_ids:
                errors.append(f"{name}: deployed_release が release/ にありません")
            # **戻せる先を必ず書く。** LEGACY の状態へ戻すときも、 その記録を指す
            if not _earlier(d.get("rollback_to"), m, seq, by_id):
                errors.append(f"{name}: rollback_to は同じ機体の前の記録の ID にします（戻せる先）")
            cc = d.get("config_commit")
            if not (isinstance(cc, str) and SHA_RE.fullmatch(cc)):
                errors.append(f"{name}: config_commit は 40 桁の commit SHA にします")
            arts = d.get("artifacts")
            if not (isinstance(arts, list) and arts and all(
                    isinstance(a, dict) and a.get("name") and a.get("version") not in (None, "")
                    and isinstance(a.get("sha256"), str) and HEX64_RE.fullmatch(a["sha256"]) for a in arts)):
                errors.append(f"{name}: artifacts は name・version・sha256（64 桁）の並びにします")
            ms = d.get("model_sha256")
            if not (isinstance(ms, str) and HEX64_RE.fullmatch(ms)):
                errors.append(f"{name}: model_sha256 は 64 桁の sha256 にします")
            pkg = _package_of(releases.get(str(rel))) if rel else None
            if pkg is not None:
                # **前の記録と同じ System Release のまま書いた記録（ずれの確かめ）は、 配置を変えない。**
                #   戻せる先が今の状態を指したり、 配った物が黙って変わったりしないように、 前の記録と同じにする
                #   （前の形の記録には当てない。 前の形の扱いは変えない）
                prev = by_id.get(f"{m}-{seq - 1:04d}")
                if (isinstance(prev, dict) and prev.get("status") == "ACTIVE"
                        and prev.get("deployed_release") == rel and rb is None):
                    for k in SAME_STATE_FIELDS:
                        if d.get(k) != prev.get(k):
                            errors.append(f"{name}: 前の記録と同じ System Release の記録（ずれの確かめ）は、{k} を前の"
                                          "記録と同じにします（配置を変えたときは、配った記録として書く）")
                # 製品のパッケージのある System Release を配った記録は、 組み立て器が見たずれと、 その wheel を持つ
                if "recipe_drift" not in d:
                    errors.append(f"{name}: 製品のパッケージのある System Release を配った記録には recipe_drift を書きます"
                                  "（ずれが無ければ products: {}）")
                else:
                    _check_drift(name, d.get("recipe_drift"), errors)
                want = (_norm_name(str(pkg.get("name", ""))), str(pkg.get("version", "")), pkg.get("wheel_sha256"))
                if isinstance(arts, list) and not any(
                        isinstance(a, dict) and (_norm_name(str(a.get("name", ""))), str(a.get("version", "")),
                                                 a.get("sha256")) == want for a in arts):
                    errors.append(f"{name}: artifacts に、配った System Release の製品のパッケージの wheel"
                                  "（product_package の name・version・wheel_sha256）がありません")
                continue
            if not rel or str(rel) not in known_ids:
                continue                            # 配った System Release が無いことは、 上で言った
        if "recipe_drift" in d:
            errors.append(f"{name}: recipe_drift は、製品のパッケージのある System Release を配った ACTIVE の記録にだけ書きます")
    if any("recipe_drift" in d for d in by_id.values()):
        latest = _latest(by_id)
        drifted = sorted(m for m, d in latest.items() if d.get("status") == "ACTIVE"
                         and isinstance(d.get("recipe_drift"), dict) and d["recipe_drift"].get("products"))
        notes.append(f"ずれ（recipe_drift）: 一番新しい記録にずれが残る機体 {len(drifted)} 台"
                     + (f"（{'・'.join(drifted)}）" if drifted else ""))
    if by_machine:
        latest = [by_id.get(f"{m}-{max(s):04d}") or {} for m, s in sorted(by_machine.items())]
        notes.append(f"配置の記録（schema 2）: 機体 {len(latest)} 台・今の状態 " + "・".join(
            f"{s} {sum(1 for d in latest if d.get('status') == s)}" for s in DEPLOY_STATUSES))
    return by_id


def _vr_order(vid: str) -> tuple:
    _, _, year, seq = vid.split("-")
    return int(year), int(seq)


def _check_verifications(recs: list, root: Path, known_ids: set, deployments: dict,
                         errors: list, notes: list) -> None:
    """検証の記録。 結果は検証の記録にだけ書き、 状態は対象 × 手順の ID ごとに一番新しい記録で決まる。"""
    by_id = {}
    for p, d in recs:
        name = p.name
        vid = d.get("verification_id")
        if not (isinstance(vid, str) and VR_ID_RE.fullmatch(vid)):
            errors.append(f"{name}: verification_id は VR-<案件の記号>-<年 4 桁>-<連番 3 桁> にします")
            continue
        if p.stem != vid:
            errors.append(f"{name}: ファイル名は verification_id と同じ {vid}.yaml にします")
        if d.get("schema_version") != 1:
            errors.append(f"{name}: schema_version は 1 にします")
            continue
        scope, tgt, key = d.get("scope"), d.get("target"), None
        if scope not in VR_SCOPES:
            errors.append(f"{name}: scope は {' / '.join(VR_SCOPES)} のどれかにします")
        elif not isinstance(tgt, dict):
            errors.append(f"{name}: target の節がありません")
        elif scope == "deployment":
            dep = tgt.get("deployment")
            if dep not in deployments:
                errors.append(f"{name}: target.deployment が deployment/ の schema 2 の記録にありません")
            elif d.get("machine") != deployments[dep].get("machine"):
                errors.append(f"{name}: machine が配置の記録の機体と違います")
            else:
                key = f"deployment:{dep}"
        elif scope == "system":
            sr = tgt.get("system_release")
            if not sr or str(sr) not in known_ids:
                errors.append(f"{name}: target.system_release が release/ にありません")
            else:
                key = f"system:{sr}"
        else:
            pr = tgt.get("product")
            if not (isinstance(pr, dict) and pr.get("name") and isinstance(pr.get("tag"), str)
                    and TAG_RE.fullmatch(pr["tag"]) and isinstance(pr.get("commit"), str)
                    and SHA_RE.fullmatch(pr["commit"])):
                errors.append(f"{name}: target.product に name・tag（vX.Y.Z）・commit（40 桁）を書きます")
            else:
                key = f"product:{pr['name']}@{pr['tag']}"
        proto = d.get("protocol")
        if not (isinstance(proto, dict) and proto.get("id") and proto.get("version") not in (None, "")):
            errors.append(f"{name}: protocol に id と version を書きます（手順は validation/protocols/）")
            key = None
        if d.get("environment") not in VR_ENVIRONMENTS:
            errors.append(f"{name}: environment は {' / '.join(VR_ENVIRONMENTS)} のどれかにします")
        dt = d.get("date")
        if not (isinstance(dt, datetime.date) or (isinstance(dt, str) and DATE_RE.fullmatch(dt))):
            errors.append(f"{name}: date は YYYY-MM-DD にします")
        _person(name, d, "verifier", errors)
        res, cases = d.get("result"), d.get("cases")
        if res not in VR_RESULTS:
            errors.append(f"{name}: result は {' / '.join(VR_RESULTS)} のどれかにします")
        if not (isinstance(cases, list) and cases and all(
                isinstance(c, dict) and c.get("id") and c.get("result") in VR_RESULTS for c in cases)):
            errors.append(f"{name}: cases は id と result（{' / '.join(VR_RESULTS)}）の並びにします")
        elif res in VR_RESULTS:
            # **項目と全体の結果を食い違わせない。** 「たぶん大丈夫」を通さない
            rs = {c["result"] for c in cases}
            want = "FAIL" if "FAIL" in rs else ("PARTIAL" if "PARTIAL" in rs else "PASS")
            if res != want:
                errors.append(f"{name}: result が項目の結果と合いません（項目からは {want}）")
        ev = d.get("evidence")
        if not isinstance(ev, list):
            errors.append(f"{name}: evidence は並びにします（無ければ []）")
        else:
            for e in ev:
                ok = False
                if isinstance(e, dict) and e.get("type") == "file" and isinstance(e.get("path"), str):
                    pp = PurePosixPath(e["path"])
                    ok = not pp.is_absolute() and ".." not in pp.parts and (root / e["path"]).is_file()
                elif isinstance(e, dict) and e.get("type") == "storage":
                    ok = bool(e.get("id")) and isinstance(e.get("sha256"), str) and bool(HEX64_RE.fullmatch(e["sha256"]))
                if not ok:
                    errors.append(f"{name}: evidence は type: file（この Repository の中にあるファイルの道）か"
                                  " type: storage（id と 64 桁の sha256）にします")
                    break
        if key:
            by_id[vid] = (key, str(proto["id"]), d.get("supersedes"), res)
    # **supersedes は、 同じ対象と手順の ID の、 前の番号の記録だけ。** 枝分かれを許さない
    replaced = {}
    for vid, (key, pid, sup, _) in sorted(by_id.items()):
        if sup is None:
            continue
        if not (isinstance(sup, str) and sup in by_id and by_id[sup][:2] == (key, pid)
                and _vr_order(sup) < _vr_order(vid)):
            errors.append(f"{vid}.yaml: supersedes は同じ対象と手順の ID の、 前の番号の検証の記録にします")
        elif sup in replaced:
            errors.append(f"{vid}.yaml: {sup} を置き換える記録が 2 つあります（{replaced[sup]} と）")
        else:
            replaced[sup] = vid
    heads = {}
    for vid, (key, pid, _, _) in sorted(by_id.items()):
        if vid in replaced:
            continue
        if (key, pid) in heads:
            errors.append(f"{vid}.yaml: 同じ対象と手順の ID に、 置き換えられていない記録が 2 つあります（{heads[(key, pid)]} と）")
        heads[(key, pid)] = vid
    if heads:
        states = [{"PASS": "VERIFIED", "FAIL": "FAILED"}.get(by_id[v][3], "NOT VERIFIED") for v in heads.values()]
        notes.append(f"検証の記録: 対象 × 手順 {len(heads)} 組・" + "・".join(
            f"{s} {states.count(s)}" for s in ("VERIFIED", "FAILED", "NOT VERIFIED")))


def _base_release_names(root: Path, base: str) -> "set | None":
    """基準の commit にあった `release/` のファイルの名前。 基準を読めなければ None。

    **PR の中身を実行しない。** 読むのは git の中の基準の木の名前だけ（`ls-tree`）。
    """
    try:
        ok = subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{base}^{{commit}}"],
                            capture_output=True)
        if ok.returncode != 0:
            return None
        r = subprocess.run(["git", "-C", str(root), "ls-tree", "-z", "--name-only", f"{base}^{{commit}}",
                            "--", "release/"], capture_output=True)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return {PurePosixPath(n.decode("utf-8", "surrogateescape")).name for n in r.stdout.split(b"\0") if n}


def _check_added_releases(added: list, releases: dict, by_id: dict, errors: list) -> None:
    """足した System Release（新しい形）ごとに、 製品のパッケージと、 機体のずれ無しを照らす（#173）。

    **ずれを残したまま、 次の System Release を作らない。** 製品のパッケージのある System Release を
    配って今 ACTIVE の機体のすべてで、 一番新しい記録が、 足す System Release のレシピと機体の設定の
    commit（`inspection.config_commit`）と比べた、 ずれ無しの記録でなければならない。
    """
    latest = _latest(by_id)
    for name, d in added:
        if _package_of(d) is None:
            errors.append(f"{name}: 新しい形で足す System Release には product_package（製品のパッケージ）を書きます")
            continue
        ins = d.get("inspection")
        cc = ins.get("config_commit") if isinstance(ins, dict) else None
        for m, rec in sorted(latest.items()):
            if rec.get("status") != "ACTIVE" or _package_of(releases.get(str(rec.get("deployed_release")))) is None:
                continue                    # 前の形の機体は、 移すときに人が照らす（ここでは見ない）
            drift = rec.get("recipe_drift")
            if not (isinstance(drift, dict) and isinstance(cc, str) and SHA_RE.fullmatch(cc)
                    and drift.get("compared_commit") == cc and drift.get("products") == {}):
                errors.append(f"{name}: 機体 {m} の一番新しい配置の記録（{rec.get('record_id')}）が、この System Release の"
                              " inspection.config_commit と比べた、ずれの無い記録ではありません（ずれを書き戻すか、人が"
                              "名指しして上書きし、ずれの確かめの記録を足してから System Release を作る）")


USAGE = ("使い方: release_record_check.py [--base-ref <基準の commit>]\n"
         "        release_record_check.py --dev-pin <versions.dev.yaml の道>")
DEV_PIN_USAGE = USAGE


def dev_pin(args: list) -> int:
    """`versions.dev.yaml` の 1 ファイルだけを、 記録の検査と同じ決まり（`_check_versions_dev`）で確かめる（#166）。

    **形どおりのときだけ値を出す。** 違えば標準出力に何も出さないので、 呼ぶ側は
    終了コードを見ずに読んでも、 確かめていない値を使わない。
    """
    if len(args) != 1 or not args[0] or args[0].startswith("-"):
        print(DEV_PIN_USAGE, file=sys.stderr)
        return 2
    p = Path(args[0])
    if not p.is_file():
        print(f"NG: {p.name or args[0]} がありません（ファイルの道を渡してください）", file=sys.stderr)
        return 1
    try:
        import yaml
    except ImportError:
        print("PyYAML が無いので確かめられません。"
              " `python -m pip install pyyaml` を実行してください。", file=sys.stderr)
        return 2
    errors: list = []
    notes: list = []
    d = _check_versions_dev(p, errors, notes, yaml)
    if errors or d is None:
        print(f"NG: {len(errors)} 件", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print("\n  形式は MirumeAI/development-docs の"
              " 03_customer_development/release_and_deployment.md を"
              "参照してください。", file=sys.stderr)
        return 1
    # 確かめた中身そのもの（読み直さない）。 決まりを通った値は 40 桁の 16 進だけ（改行や空白を含まない）
    for key in VERSIONS_CORES:
        if key in d:
            print(f"{key}={d[key]}")
    for n in notes:
        print(n, file=sys.stderr)
    print(f"OK: {p.name} は形どおりです", file=sys.stderr)
    return 0


def main(argv: "list | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--dev-pin":
        return dev_pin(args[1:])
    base = None
    if len(args) == 2 and args[0] == "--base-ref" and _HEX_REF.fullmatch(args[1]):
        base = args[1]
    elif args:
        # **知らない引数で、 黙ってすべての記録の検査へ進まない**（基準は commit の 16 進 7〜40 字だけ）
        print(USAGE, file=sys.stderr)
        return 2
    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    rel_dir, dep_dir = root / "release", root / "deployment"
    versions = root / "versions.yaml"
    versions_dev = root / VERSIONS_DEV
    val_dir = root / "validation"
    releases, deployments = _records(rel_dir), _records(dep_dir)
    verifications = _records(val_dir)

    if (not rel_dir.is_dir() and not dep_dir.is_dir() and not val_dir.is_dir()
            and not versions.is_file() and not versions_dev.is_file()):
        print("release/・deployment/・validation/・versions.yaml が無いので何もしません。")
        return 0

    # **検査できないのに OK と言わない。**
    #   記録が無ければ何もしないが、 記録があるのに読めないときは
    #   「合格」ではなく「確かめられなかった」と返す。
    try:
        import yaml
    except ImportError:
        if (not releases and not deployments and not verifications and not versions.is_file()
                and not versions_dev.is_file()):
            print("記録がまだありません。")
            return 0
        print("PyYAML が無いので記録を検査できません。"
              " `python -m pip install pyyaml` を実行してください。",
              file=sys.stderr)
        return 2

    errors: list = []
    notes: list = []
    unchecked: list = []
    known_ids = set()
    rel_by_id: dict = {}
    rel_by_file: dict = {}
    # 新しい形の印（リンクやフォルダでも、 在れば新しい形として扱う。 形の誤りは混入の検査が止める）
    case = root / CASE_FILE
    new_form = case.exists() or case.is_symlink()

    if versions.is_file():
        _check_versions(versions, errors, notes, yaml)
    if versions_dev.is_file():
        _check_versions_dev(versions_dev, errors, notes, yaml)

    for p in releases:
        d = _load(p, errors, yaml)
        if d is None:
            continue
        _check_schema(p.name, d, errors)
        _check_fields(p.name, d, RELEASE_FIELDS, errors)
        _check_release_data(p.name, d, errors)
        _check_product_package(p.name, d, new_form, errors)
        _dev_refs(p.name, d, errors)
        rel_by_file[p.name] = d
        # **System Release は versions.yaml だけを参照する**（#153）。 開発中の固定
        # （versions.dev.yaml）や別のファイルを指すと、 タグの無い版が配置に流れる。
        sw = d.get("software")
        ref = sw.get("versions_ref") if isinstance(sw, dict) else None
        if ref not in (None, "") and ref != VERSIONS_REF:
            errors.append(f"{p.name}: software.versions_ref は {VERSIONS_REF} です")
        rid = d.get("release_id")
        if rid:
            known_ids.add(str(rid))
            rel_by_id[str(rid)] = d
            # **ファイル名と ID が違うと、 どちらが正しいか分からなくなる。**
            if str(rid) != p.stem:
                errors.append(
                    f"{p.name}: release_id が {rid!r} でファイル名と違います"
                    f"（ファイル名は {p.stem!r}）")

    dep2: list = []
    for p in deployments:
        d = _load(p, errors, yaml)
        if d is None:
            continue
        _dev_refs(p.name, d, errors)
        if d.get("schema_version") == 2:
            # **schema 2（P1-05）は機体ごとの記録の並びで見る**（下でまとめて）
            dep2.append((p, d))
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

    by_record = _check_deployments_v2(dep2, known_ids, errors, notes, rel_by_id)
    # **ずれを残したまま、 次の System Release を作らない**（#173）。 照らすのは、 この Pull Request で
    #   足した System Release だけ（足した後の配置の記録を止めない）。 見分けには基準の commit が要る
    if new_form and rel_by_file:
        if base is None:
            notes.append("足した System Release の照らし: していません（--base-ref がありません）")
        else:
            before = _base_release_names(root, base)
            if before is None:
                unchecked.append("基準の commit を読めないので、この Pull Request で足した System Release を"
                                 "見分けられません（ずれ無しを確かめていません）")
            else:
                added = [(n, d) for n, d in sorted(rel_by_file.items()) if n not in before]
                _check_added_releases(added, rel_by_id, by_record, errors)
                notes.append(f"足した System Release の照らし: {len(added)} 件")
    vrs = []
    for p in verifications:
        d = _load(p, errors, yaml)
        if d is not None:
            vrs.append((p, d))
    _check_verifications(vrs, root, known_ids, by_record, errors, notes)

    print(f"検査: release {len(releases)} 件 / deployment {len(deployments)} 件"
          f" / validation {len(verifications)} 件")
    for n in notes:
        print(n)
    if errors:
        print(f"\nNG: {len(errors)} 件", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        print("\n  形式は MirumeAI/development-docs の"
              " 03_customer_development/release_and_deployment.md を"
              "参照してください。", file=sys.stderr)
        for u in unchecked:
            print(f"  確かめていません: {u}", file=sys.stderr)
        return 1
    if unchecked:
        # **確かめられなかったのに OK と言わない**
        for u in unchecked:
            print(f"確かめられません: {u}", file=sys.stderr)
        return 2
    print("OK: 記録は形式どおりです")
    return 0


if __name__ == "__main__":
    sys.exit(main())

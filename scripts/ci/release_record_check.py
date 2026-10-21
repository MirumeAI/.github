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
        errors.append(f"{name}: {label} の {value!r} は決まった名前の形ではありません")
    elif code and m.group(1) != code:
        errors.append(f"{name}: {label} の案件の記号が release_id と違います（別の案件の物を混ぜない）")


def _check_release_data(name: str, d: dict, errors: list) -> None:
    """採用したモデルと Dataset の版・数・大きさ・manifest の sha256（P1-07）。"""
    m = RELEASE_ID_RE.fullmatch(str(d.get("release_id") or ""))
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


def _check_deployments_v2(recs: list, known_ids: set, errors: list, notes: list) -> dict:
    """配置の記録 schema 2。 機体ごとの並びを確かめ、 記録の ID → 中身を返す。"""
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


def main() -> int:
    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    rel_dir, dep_dir = root / "release", root / "deployment"
    versions = root / "versions.yaml"
    val_dir = root / "validation"
    releases, deployments = _records(rel_dir), _records(dep_dir)
    verifications = _records(val_dir)

    if (not rel_dir.is_dir() and not dep_dir.is_dir() and not val_dir.is_dir()
            and not versions.is_file()):
        print("release/・deployment/・validation/・versions.yaml が無いので何もしません。")
        return 0

    # **検査できないのに OK と言わない。**
    #   記録が無ければ何もしないが、 記録があるのに読めないときは
    #   「合格」ではなく「確かめられなかった」と返す。
    try:
        import yaml
    except ImportError:
        if not releases and not deployments and not verifications and not versions.is_file():
            print("記録がまだありません。")
            return 0
        print("PyYAML が無いので記録を検査できません。"
              " `python -m pip install pyyaml` を実行してください。",
              file=sys.stderr)
        return 2

    errors: list = []
    notes: list = []
    known_ids = set()

    if versions.is_file():
        _check_versions(versions, errors, notes, yaml)

    for p in releases:
        d = _load(p, errors, yaml)
        if d is None:
            continue
        _check_schema(p.name, d, errors)
        _check_fields(p.name, d, RELEASE_FIELDS, errors)
        _check_release_data(p.name, d, errors)
        rid = d.get("release_id")
        if rid:
            known_ids.add(str(rid))
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

    by_record = _check_deployments_v2(dep2, known_ids, errors, notes)
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
        return 1
    print("OK: 記録は形式どおりです")
    return 0


if __name__ == "__main__":
    sys.exit(main())

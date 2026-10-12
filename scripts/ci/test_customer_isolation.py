#!/usr/bin/env python3
"""`customer_isolation_check.py` が、 別の案件の値の混入を見つけることを確かめる。

起きたこと（顧客リポジトリの最初の登録）: 撮像側の別のブランチの設定を
そのまま写し、 別の案件の構成のファイル 1 つと、 別の案件の PLC の表
（`plc.enabled` は false）が入った。 名前の検査では見つからず、 配る道具の
確かめも通った。 ここでは、 それを **架空の 2 つの案件** で再現する。

    案件 A  PLC なし・カメラ 1 台（混ざられる側）
    案件 B  PLC あり・カメラ 5 台・トリガと番地を持つ（混ざる側）

**試験の値はすべて架空で、 実行するときに組み立てる。** 実在の値から作らない。

  ・IP は文書用の網（RFC 5737）。 **検査はこの網を除かない**ので、 B の宛先として
    働く。 名前の検査（customer_data_check.py）は、 この網を見本として通す
  ・製造番号・番地・構成の名前は `EXAMPLE`・`example` を含む形。 トリガと面は
    `trig_example_` で始まる形

どの場面でも **出力に値が出ないこと** を見る。 最後に、 検査の守りを 1 つずつ
壊して、 どれかの場面が落ちることを確かめる（変異の試験。 撮像側と PDM の
`scripts/ci/mutation_check.py` と同じ決まり: 置換前が 1 か所だけ見つからなければ
失敗、 壊す前に全部の場面が通ることを先に確かめる）。

PyYAML が要る。 CI（環境変数 CI がある）で無ければ、 飛ばさずに落とす。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "customer_isolation_check.py"
README = HERE.parents[1] / "README.md"
WORKFLOWS = HERE.parents[1] / ".github" / "workflows"
sys.path.insert(0, str(HERE))

import customer_data_check as C                            # noqa: E402
import customer_isolation_check as I                       # noqa: E402

try:
    import yaml
except ImportError:          # CI では入れる（.github/workflows/basic-checks.yml）
    yaml = None


def _ip(net: int, host: int) -> str:
    """文書用の網（RFC 5737）の IP。 実行するときに組み立てる。"""
    return ("192.0.2.", "198.51.100.", "203.0.113.")[net] + str(host)


#: 案件 A（PLC なし・カメラ 1 台）
A_PROFILE = "example-" + "a"
A_IP = _ip(0, 10)
A_SERIAL = "EXAMPLE-SN-" + "0001"
A_TRIGGER = "trig_example_" + "a_top"

#: 案件 B（PLC あり・カメラ 5 台）。 面を分けて撮る（`_1`・`_2`）面を持つ。
B_PROFILE = "example-" + "b"
B_IPS = [_ip(1, 20 + i) for i in range(5)]
B_SERIALS = ["EXAMPLE-SN-" + f"{100 + i:04d}" for i in range(5)]
B_PLC_HOST = _ip(2, 5)
B_PLC_PORT = 5000 + 10
B_TRIGGERS = ["trig_example_" + s for s in ("b_front_1", "b_front_2", "b_side", "b_back")]
B_GROUP = "group_example_" + "1"
#: 混ざった構成のファイルの名前（B の構成の名前を含む）。
B_FILE = "config." + B_PROFILE + ".json"

#: 宣言に書いてはいけない値（試験の中だけで組み立てる）。
CREDENTIAL = "example-" + "not-a-real-password"
TOKEN = "ghp" + "_" + "Ab3" * 12


def b_plc(enabled: bool = True) -> dict:
    """B の PLC の表（信号の名前は架空。 形だけ Core と同じ）。"""
    n = iter(range(100, 1000))

    def sig() -> dict:
        return {"bit": 0, "data": "EXAMPLE_ADDR_" + str(next(n))}

    return {
        "enabled": enabled,
        "address": {"host": B_PLC_HOST, "port": B_PLC_PORT},
        "plc_to_pc": {
            "shot_request": {t: {"trig_bit": sig()} for t in B_TRIGGERS},
            "result_slot": {B_GROUP: {"device": sig(), "triggers": list(B_TRIGGERS)}},
        },
        "pc_to_plc": {
            "example_signal": sig(),
            "shot_request": {"example_ready": sig(),
                             **{t: {"trig_ready": sig()} for t in B_TRIGGERS}},
        },
    }


def _addresses(node) -> "list[str]":
    if isinstance(node, dict):
        return [a for k, v in node.items()
                for a in ([v] if k == "data" else _addresses(v))]
    return []


B_ADDRS = _addresses(b_plc())

#: 出力に出てはいけない値（どの場面でも見る）。
NOT_SHOWN = (A_PROFILE, A_IP, A_SERIAL, A_TRIGGER, B_PROFILE, *B_IPS, *B_SERIALS,
             B_PLC_HOST, *B_TRIGGERS, B_GROUP, *B_ADDRS, CREDENTIAL, TOKEN)


def a_config() -> dict:
    return {
        "_profile": A_PROFILE,
        "flow": "label",
        "camera": {"camera1": {"camera_type": "area", "ip_address": A_IP,
                               "serial_number": A_SERIAL}},
        "shot": {"camera1": {"camera_type": "area", "triggers": [A_TRIGGER]}},
        "face_dict": {A_TRIGGER: "上面"},
        "nas": {"enabled": False},
        "plc": {"enabled": False},
        "ai": {"enabled": True},
        # 一般の値（宣言しなくてよい）
        "server": {"listen": "0.0.0.0", "local": "127.0.0.1", "netmask": "255.255.255.0"},
    }


def a_profile() -> dict:
    return {
        "schema_version": 1,
        "project": {"flow": "label", "profiles": [A_PROFILE]},
        "capabilities": {"plc": False, "nas": False, "ai": True},
        "equipment": {"cameras": [{"id": "camera1", "ip": A_IP, "serial": A_SERIAL}]},
        "triggers": [A_TRIGGER],
        "faces": [A_TRIGGER],
    }


def b_config() -> dict:
    return {
        "_profile": B_PROFILE,
        "flow": "metal",
        "camera": {f"camera{i + 1}": {"ip_address": B_IPS[i], "serial_number": B_SERIALS[i]}
                   for i in range(5)},
        "shot": {f"camera{i + 1}": {"triggers": [B_TRIGGERS[i % len(B_TRIGGERS)]]}
                 for i in range(5)},
        "face_dict": {t: "面" for t in B_TRIGGERS},
        "nas": {"enabled": True},
        "plc": b_plc(True),
        "ai": {"enabled": True},
    }


def b_profile() -> dict:
    return {
        "schema_version": 1,
        "project": {"flow": "metal", "profiles": [B_PROFILE]},
        "capabilities": {"plc": True, "nas": True, "ai": True},
        "equipment": {"cameras": [{"id": f"camera{i + 1}", "ip": B_IPS[i],
                                   "serial": B_SERIALS[i]} for i in range(5)]},
        "plc": {"endpoints": [f"{B_PLC_HOST}:{B_PLC_PORT}"], "addresses": list(B_ADDRS)},
        # 同じ list を 2 か所に渡すと、 safe_dump が別名（*）で書く（検査は断る）
        "triggers": list(B_TRIGGERS),
        "faces": list(B_TRIGGERS),
    }


def _json(d) -> str:
    return json.dumps(d, ensure_ascii=False, indent=2) + "\n"


def _yaml(d) -> str:
    return yaml.safe_dump(d, allow_unicode=True, sort_keys=False)


IA = "config/image-acquisition/"
SERVICE = "deploy/image-acquisition/example.service"


def a_files(profile: "dict | None" = None, config: "dict | None" = None) -> dict:
    """案件 A のリポジトリ（混ざる前）。"""
    return {
        "customer-profile.yaml": _yaml(profile or a_profile()),
        "config/README.md": "# config/\n",
        IA + "config.json": _json(config or a_config()),
        IA + "pdm_guard_policy.json": _json({"local_only": ["127.0.0.1"]}),
        "config/pdm/capture/.gitkeep": "",
        "config/pdm/bbox_acquire/" + A_TRIGGER + ".json": _json({"bbox": [0, 0, 10, 10]}),
        SERVICE: ("# 案件の設定は config/image-acquisition/config.json\n"
                  "ExecStart=/usr/bin/true --host 0.0.0.0\n"),
        "versions.yaml": "schema_version: 1\n",
    }


def b_files() -> dict:
    """案件 B のリポジトリ（PLC あり）。 面ごとの設定は基の名前でも持つ。"""
    front = B_TRIGGERS[0].rsplit("_", 1)[0]          # 基の名前（末尾の _<数字> を落とす）
    return {
        "customer-profile.yaml": _yaml(b_profile()),
        IA + "config.json": _json(b_config()),
        "config/pdm/bbox_acquire/" + front + ".json": _json({"bbox": [1, 2, 3, 4]}),
        "config/pdm/adaptive_mask/" + front + "__region1.json": _json({"of": B_TRIGGERS[0]}),
        "config/pdm/good_pool/" + B_TRIGGERS[2] + "__region1.json":
            _json({"images": [B_TRIGGERS[2] + "/0001.png"]}),
        "config/pdm/align_reference.txt": "EXAMPLE-LOT-0001\n",
        "versions.yaml": "schema_version: 1\n",
    }


def _with(files: dict, **changes) -> dict:
    """道 → 中身（None は消す）を変えた写し。 鍵は `__` で道の `/` を表す。"""
    out = dict(files)
    for k, v in changes.items():
        rel = k.replace("__", "/")
        if v is None:
            out.pop(rel, None)
        else:
            out[rel] = v
    return out


class Link:
    """場面の中のリンク（ファイルの中身の代わりに置く）。"""

    def __init__(self, target: str) -> None:
        self.target = target


class Scenario:
    """1 つの場面。 `commits` は commit ごとのファイルの全体（最後が検査の相手）。"""

    def __init__(self, name: str, commits: list, exit: int, codes=(), exact: bool = False,
                 error: "str | None" = None, args=(), texts=(), absent=()) -> None:
        self.name, self.commits, self.exit = name, commits, exit
        self.codes, self.exact, self.error = set(codes), exact, error
        self.args, self.texts, self.absent = tuple(args), tuple(texts), tuple(absent)


def scenarios() -> "list[Scenario]":
    s: "list[Scenario]" = []

    def add(name, files, exit, codes=(), **kw) -> None:
        commits = files if isinstance(files, list) else [files]
        s.append(Scenario(name, commits, exit, codes, **kw))

    def a_cfg(**kw) -> dict:
        c = a_config()
        c.update(kw)
        return c

    def a_prof(**kw) -> dict:
        p = a_profile()
        p.update(kw)
        return p

    # ---- 指示の 1〜13（起きたことの再現と、 止める場面）
    add("1. 案件 A の正しい設定（一般の値を含む）", a_files(), 0, exact=True)
    add("2. A に B の構成のファイルをまるごと足す", _with(a_files(), **{
        "config__image-acquisition__" + B_FILE: _json(b_config())}), 1,
        {"S1-file", "S2-plc", "S3-profile", "L1-endpoint", "L1-trigger"})
    add("2b. 値の無い知らない設定のファイルも黙って通さない", _with(a_files(), **{
        "config__image-acquisition__settings.json": _json({"memo": "x"})}), 1,
        {"S1-file"}, exact=True)
    add("3. PLC を使わない A に B の PLC の表（enabled は false）",
        a_files(config=a_cfg(plc=b_plc(False))), 1,
        {"S2-plc", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    cfg = a_config()
    cfg["camera"]["camera1"]["ip_address"] = B_IPS[0]
    add("4. A のカメラの宛先を B のものにする", a_files(config=cfg), 1,
        {"L1-endpoint"}, exact=True)
    cfg = a_config()
    cfg["shot"]["camera1"]["triggers"].append(B_TRIGGERS[2])
    add("5. A のトリガに B のものを足す", a_files(config=cfg), 1, {"L1-trigger"}, exact=True)
    add("6. A の構成の名前を B のものにする", a_files(config=a_cfg(_profile=B_PROFILE)), 1,
        {"S3-profile"}, exact=True)
    add("7. deploy と versions.yaml が、 無い設定のファイルに触れる", _with(a_files(), **{
        "deploy__image-acquisition__example.service":
            f"# 案件の設定は config/image-acquisition/{B_FILE}\n",
        "versions.yaml": f"schema_version: 1\n# {B_FILE} を読む\n"}), 1,
        {"S4-missing"}, exact=True)
    add("7b. deploy が、 許されていない設定のファイルに触れる", _with(a_files(), **{
        "config__image-acquisition__settings.json": _json({"memo": "x"}),
        "deploy__image-acquisition__example.service":
            "# 設定は config/image-acquisition/settings.json\n"}), 1,
        {"S1-file", "S4-not-allowed"}, exact=True)
    add("8. 宣言が無い", _with(a_files(), **{"customer-profile.yaml": None}), 2,
        error="profile-missing")
    add("9. 宣言が YAML として読めない", _with(a_files(), **{
        "customer-profile.yaml": "schema_version: [1\n"}), 2, error="profile-yaml")
    add("10. 資格情報らしい鍵", _with(a_files(), **{
        "customer-profile.yaml": _yaml(a_profile()) + f"password: {CREDENTIAL}\n"}), 2,
        error="profile-credential")
    add("10b. 資格情報らしい値", a_files(profile=a_prof(extensions=[TOKEN])), 2,
        error="profile-credential")
    add("11. B の正しい設定（PLC の表・Core の鍵・基の名前の面のファイル）", b_files(), 0,
        exact=True)
    add("11b. 補い: 決まった場所の外の、 別の案件のトリガ（JSON の鍵）", _with(a_files(), **{
        "config__image-acquisition__recipe.json": _json({"faces": {B_TRIGGERS[1]: {"n": 1}}})}),
        1, {"L1-name"}, exact=True)
    add("11c. 補い: 決まった場所の外の、 別の案件の宛先（JSON の値）", _with(a_files(), **{
        "config__image-acquisition__machine.json": _json({"nas_host": B_IPS[1]})}), 1,
        {"L1-endpoint"}, exact=True)
    add("11d. 補い: .txt の中の、 別の案件のトリガ", _with(a_files(), **{
        "config__pdm__align_reference.txt": B_TRIGGERS[2] + "\n"}), 1, {"L1-name"}, exact=True)
    # 12 は、 どの場面でも見る（出力に値が出ない）。
    grown = a_prof(endpoints=[_ip(0, 99)],
                   triggers=[A_TRIGGER] + ["trig_example_" + x for x in ("n1", "n2", "n3")])
    add("13. 宣言の増え方は、 種類と数だけを出す", [
        a_files(profile=a_prof(triggers=[A_TRIGGER, "trig_example_old"])),
        a_files(profile=grown)], 0, exact=True, args=("--base-ref", "{base}"),
        texts=("PROFILE EXPANSION DETECTED", "endpoints: +1 / -0", "triggers: +3 / -1"))

    # ---- 宣言の増え方（そのほか）
    add("13b. 宣言が新しく作られた", [_with(a_files(), **{"customer-profile.yaml": None}),
                                   a_files()], 0, exact=True, args=("--base-ref", "{base}"),
        texts=("この変更で作られました", "PROFILE EXPANSION DETECTED"))
    add("13c. 宣言が変わらない", [a_files(), _with(a_files(), **{"config__README.md": "# x\n"})],
        0, exact=True, args=("--base-ref", "{base}"), texts=("変化なし",),
        absent=("PROFILE EXPANSION DETECTED",))
    add("13d. 基準の commit が手元に無い", a_files(), 0, exact=True,
        args=("--base-ref", "0" * 40), texts=("比べていません",))
    add("13e. --base-ref の形が違う", a_files(), 2, error="usage", args=("--base-ref", "main"))

    # ---- 宛先の決まり（ちょうどの値が原則。 網は理由つきで、 狭いものだけ）
    p = a_profile()
    p["equipment"]["cameras"][0].pop("ip")
    net = {"network": _ip(0, 0) + "/28", "reason": "架空の案件の、 カメラの網"}
    add("網で許す（理由つき・/28）", a_files(profile=dict(p, endpoints=[net])), 0, exact=True)
    add("広すぎる網は断る", a_files(profile=dict(p, endpoints=[
        {"network": "192.0.0.0/16", "reason": "広すぎる"}])), 2, error="profile-schema")
    add("網に理由が無い", a_files(profile=dict(p, endpoints=[{"network": net["network"]}])), 2,
        error="profile-schema")
    add("網を文字で書く", a_files(profile=dict(p, endpoints=[net["network"]])), 2,
        error="profile-schema")

    # ---- S1〜S3 のそのほか
    add("defaults/ に案件の値を置く", _with(a_files(), **{
        "config__pdm__defaults__x.json": _json({})}), 1, {"S1-defaults"}, exact=True)
    add("config/ の下のリンク", _with(a_files(), **{
        "config__image-acquisition__link.json": Link("../../versions.yaml")}), 1,
        {"S1-link"}, exact=True)
    add("中身のある .gitkeep", _with(a_files(), **{"config__pdm__capture__.gitkeep": "x\n"}), 1,
        {"S1-file"}, exact=True)
    add("宣言に無い面の名前のファイル（config/pdm）", _with(a_files(), **{
        "config__pdm__region_split__" + B_TRIGGERS[2] + ".json": _json({})}), 1,
        {"S3-face-file"}, exact=True)
    add("宣言に無い面の名前のファイル（撮像側の置き場）", _with(a_files(), **{
        "config__image-acquisition__" + B_TRIGGERS[3] + ".json": _json({})}), 1,
        {"S1-file"})
    cfg = a_config()
    cfg["face_dict"][B_TRIGGERS[3]] = "面"
    add("宣言に無い面（face_dict）", a_files(config=cfg), 1, {"S3-face"}, exact=True)
    cfg = a_config()
    cfg["camera"]["camera2"] = {"camera_type": "area"}
    add("宣言に無いカメラ", a_files(config=cfg), 1, {"S3-camera"}, exact=True)
    add("flow が違う", a_files(config=a_cfg(flow="metal")), 1, {"S3-flow"}, exact=True)
    add("NAS の有無が宣言と違う", a_files(profile=a_prof(
        capabilities={"plc": False, "nas": True, "ai": True})), 1, {"S2-nas"}, exact=True)
    add("AI の有無が宣言と違う", a_files(profile=a_prof(
        capabilities={"plc": False, "nas": False, "ai": False})), 1, {"S2-ai"}, exact=True)
    bc = b_config()
    bc["plc"]["enabled"] = False
    add("PLC を使う宣言なのに plc.enabled が false",
        _with(b_files(), **{"config__image-acquisition__config.json": _json(bc)}), 1,
        {"S2-plc-enabled"}, exact=True)
    add("PLC を使わない宣言に PLC の宛先", a_files(profile=a_prof(
        plc={"endpoints": [f"{B_PLC_HOST}:{B_PLC_PORT}"]})), 2, error="profile-schema")
    add("主の設定が無い", _with(a_files(), **{"config__image-acquisition__config.json": None}),
        2, error="config-missing")
    add("主の設定の JSON が壊れている", _with(a_files(), **{
        "config__image-acquisition__config.json": "{\n"}), 2, error="config-json")
    add("nas.enabled を書いていない（推測しない）", a_files(config=a_cfg(nas={})), 2,
        error="config-field")
    extra = {"config_files": {"extra": [{"path": IA + "extra.json", "reason": "架空の追加"}]}}
    add("宣言した追加のファイル", _with(a_files(profile=a_prof(**extra)), **{
        "config__image-acquisition__extra.json": _json({"memo": "x"})}), 0, exact=True)
    add("宣言した追加のファイルが無い（注意だけ）", a_files(profile=a_prof(**extra)), 0,
        exact=True, texts=("宣言して使っていないもの",))
    add("deploy/ の中の値は参考だけ（不合格にしない）", _with(a_files(), **{
        "deploy__image-acquisition__example.service": f"Environment=HOST={B_IPS[0]}\n"}), 0,
        exact=True, texts=("参考（強制しません",))
    cfg = a_config()
    cfg["shot"]["camera1"]["triggers"].append(B_TRIGGERS[2])
    add("--report-only は 0 で終える（所見は出す）", a_files(config=cfg), 0, {"L1-trigger"},
        exact=True, args=("--report-only",), texts=("REPORT ONLY",))

    # ---- 宣言の形
    prof_text = _yaml(a_profile())
    add("同じ鍵が 2 回", _with(a_files(), **{
        "customer-profile.yaml": prof_text + f"triggers: [{B_TRIGGERS[0]}]\n"}), 2,
        error="profile-yaml")
    add("知らない鍵（鍵の名前も出さない）", _with(a_files(), **{
        "customer-profile.yaml": prof_text + f"{B_TRIGGERS[1]}: 1\n"}), 2,
        error="profile-schema")
    no_faces = {k: v for k, v in a_profile().items() if k not in ("triggers", "faces")}
    add("別名（*）は使わない", _with(a_files(), **{
        "customer-profile.yaml": _yaml(no_faces)
        + f"triggers: &t [{A_TRIGGER}]\nfaces: *t\n"}), 2, error="profile-yaml")
    return s


SCENARIOS = scenarios() if yaml is not None else []
#: 場面の名前 → (リポジトリ, commit の一覧)
REPOS: "dict[str, tuple[Path, list[str]]]" = {}
_TMP: "list[str]" = []

#: 試験の git は、 この機械の設定（hook など）を読まない。
_GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, env=_GIT_ENV).stdout.strip()


def _build(root: Path, commits: list) -> "list[str]":
    root.mkdir(parents=True)
    _git(root, "init", "-q")
    shas = []
    for snap in commits:
        for p in root.iterdir():
            if p.name == ".git":
                continue
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
        for rel, body in snap.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, Link):
                os.symlink(body.target, p)
            else:
                p.write_text(body, encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "-c", "user.name=test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "snapshot")
        shas.append(_git(root, "rev-parse", "HEAD"))
    return shas


def setUpModule() -> None:                                   # noqa: N802
    if yaml is None:
        if os.environ.get("CI"):
            raise RuntimeError("PyYAML が無いので試験できません（CI では入れてください）")
        raise unittest.SkipTest("PyYAML が無いので飛ばします（python -m pip install pyyaml）")
    base = tempfile.mkdtemp()
    _TMP.append(base)
    for i, sc in enumerate(SCENARIOS):
        root = Path(base) / f"s{i:02d}"
        try:
            REPOS[sc.name] = (root, _build(root, sc.commits))
        except OSError:
            REPOS[sc.name] = (root, [])          # リンクを作れない環境（その場面だけ飛ばす）


def tearDownModule() -> None:                                # noqa: N802
    for d in _TMP:
        shutil.rmtree(d, ignore_errors=True)


def run_in(mod, root: Path, args=()) -> "tuple[int, str]":
    """検査を同じプロセスで流す（変異の試験で何度も流すため）。"""
    buf = io.StringIO()
    saved = os.environ.pop("CI_ROOT", None)
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = mod.main(["--root", str(root), *args])
            except SystemExit as e:
                code = e.code
    finally:
        if saved is not None:
            os.environ["CI_ROOT"] = saved
    return code, buf.getvalue()


def problems(mod, sc: Scenario) -> "list[str]":
    """場面の期待と違うところ。 **値は書かない**（何が違うかだけ）。"""
    root, shas = REPOS[sc.name]
    if not shas:
        return []
    args = [a.replace("{base}", shas[0]) for a in sc.args]
    code, out = run_in(mod, root, args)
    got = set(re.findall(r"^NG \[([A-Za-z0-9-]+)\]", out, re.M))
    p = []
    if code != sc.exit:
        p.append(f"終了コードが {code}（期待は {sc.exit}）")
    if sc.exact and got != sc.codes:
        p.append(f"所見の種類が {sorted(got)}（期待は {sorted(sc.codes)}）")
    elif not sc.codes <= got:
        p.append(f"所見の種類 {sorted(sc.codes - got)} が出ていません")
    if sc.error and f"ERROR [{sc.error}]" not in out:
        p.append(f"ERROR [{sc.error}] が出ていません")
    p += [f"「{t}」が出ていません" for t in sc.texts if t not in out]
    p += [f"「{t}」が出ています" for t in sc.absent if t in out]
    leaked = sum(1 for v in NOT_SHOWN if v in out)
    if leaked:
        p.append(f"値が出力に出ています（{leaked} 種類）")
    if "Traceback" in out:
        p.append("Traceback が出ています")
    return p


def _load(path: Path):
    """検査を読み込む（`sys.path` は元に戻す）。"""
    name = "_isolation_" + path.parent.name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
        sys.modules.pop(name, None)
    return mod


class SyntheticScenariosTest(unittest.TestCase):
    """起きたことを架空の値で再現し、 1 か所ずつ崩す。"""

    def test_each_scenario(self) -> None:
        for sc in SCENARIOS:
            with self.subTest(sc.name):
                if not REPOS[sc.name][1]:
                    self.skipTest("リンクを作れません")
                self.assertEqual(problems(I, sc), [])

    def test_the_command_line(self) -> None:
        """`__main__` から流しても同じ終了コード。 `CI_ROOT` も読む。"""
        for name, want in (("1. 案件 A の正しい設定（一般の値を含む）", 0),
                           ("5. A のトリガに B のものを足す", 1),
                           ("8. 宣言が無い", 2)):
            root = REPOS[name][0]
            with self.subTest(name):
                r = subprocess.run([sys.executable, str(CHECKER)], cwd=str(HERE),
                                   capture_output=True, text=True,
                                   env=dict(os.environ, CI_ROOT=str(root)))
                self.assertEqual(r.returncode, want, r.stdout[-400:])
                self.assertFalse([v for v in NOT_SHOWN if v in r.stdout + r.stderr])

    def test_a_bad_argument_is_not_echoed(self) -> None:
        r = subprocess.run([sys.executable, str(CHECKER), "--x=" + B_TRIGGERS[0]],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertNotIn(B_TRIGGERS[0], r.stdout + r.stderr)

    def test_without_pyyaml_it_stops(self) -> None:
        """**PyYAML が無いのに合格と言わない。**"""
        root = REPOS["1. 案件 A の正しい設定（一般の値を含む）"][0]
        with mock.patch.dict(sys.modules, {"yaml": None}):
            code, out = run_in(I, root)
        self.assertEqual(code, 2)
        self.assertIn("ERROR [yaml-missing]", out)


#: (説明, 置換前, 置換後)。 置換前は検査の中に **1 か所だけ** あること。
#: **足すときは末尾に足す**（撮像側・PDM の表と同じ。 別々の作業が同じ行を触らない）。
MUTATIONS = [
    ("S1: 決まりの外のファイルを通す",
     "            code = self.s1(rel)\n", "            code = None\n"),
    ("S2: PLC を使わない案件の PLC の表を通す",
     'if not (isinstance(sec, dict) and list(sec) == ["enabled"]',
     'if False and (isinstance(sec, dict) and list(sec) == ["enabled"]'),
    ("層 1: 宣言の一覧と比べずに通す",
     "        if value in self.prof[cat]:\n", "        if True:\n"),
    ("層 1: 宛先を比べずに通す",
     "        ip = _norm_ipv4(raw)\n", "        return True\n"),
    ("層 1: trig_ で始まる語を比べずに通す",
     "        if token in CORE_TRIG_KEYS:\n", "        if True:\n"),
    ("出力: ファイルの道を伏せない",
     "            if part in SHOWN_PARTS:\n", "            if True:\n"),
    ("出力: 欄の名前を伏せない",
     '(x if x in SHOWN_KEYS else "<*>")', "x"),
    ("出力: 宣言の誤りの文で、 知らない鍵を伏せない",
     '(x if x in _SCHEMA_WORDS else "<*>")', "x"),
    ("宣言: 資格情報らしい鍵を通す",
     "if _credential_key(k.value) or _secret_value(k.value):", "if False:"),
    ("宣言: 資格情報らしい値を通す",
     "    elif _secret_value(node.value):\n", "    elif False:\n"),
    ("宣言: 広すぎる網を通す",
     "if net.prefixlen < MIN_PREFIX:", "if False:"),
    ("S3: 構成の名前を照らさない",
     'if isinstance(v, str) and v in prof["project.profiles"]:', "if True:"),
    ("S4: 参照を照らさない",
     "            self.refs(rel, text)\n", "            pass\n"),
    ("宣言の増え方: 増えても言わない",
     "    if grew:\n", "    if False:\n"),
    ("宣言: 別名（*）を通す",
     "if self.check_event(yaml.AliasEvent):", "if False:"),
    ("宣言: 同じ鍵の重複を通す",
     "            if k.value in seen:\n", "            if False:\n"),
    ("S2: NAS・AI の有無を照らさない",
     "elif isinstance(enabled, bool) and enabled is not prof.caps[cap]:", "elif False:"),
    ("S3: 面ごとの設定のファイルの名前を照らさない",
     'if len(parts) != 4 or not self._face_file_name(parts[3].rsplit(".", 1)[0]):',
     "if False:"),
    ("S4: deploy/ の中の値を参考でなく不合格にする",
     "    if not found:\n        print(\"結果: PASS",
     "    if not found and not notes:\n        print(\"結果: PASS"),
]


class MutationTest(unittest.TestCase):
    """**検査の守りを 1 つ壊すと、 どれかの場面が落ちること。** 落ちなければ、
    その守りは試験で確かめられていない。"""

    def _all(self, mod) -> "dict[str, list[str]]":
        return {sc.name: problems(mod, sc) for sc in SCENARIOS}

    def test_breaking_a_guard_fails_a_scenario(self) -> None:
        src = CHECKER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            def copy(tag: str, text: str):
                d = Path(td) / tag
                d.mkdir()
                (d / CHECKER.name).write_text(text, encoding="utf-8")
                return _load(d / CHECKER.name)

            # 壊す前に、 写しで全部の場面が通ること（壊す前から落ちる場面は数えない）
            before = {k: v for k, v in self._all(copy("base", src)).items() if v}
            self.assertEqual(before, {}, "壊す前から落ちる場面があります")
            for i, (what, old, new) in enumerate(MUTATIONS):
                with self.subTest(what):
                    self.assertEqual(src.count(old), 1, "置換前が 1 か所だけ見つかりません（表が古い）")
                    failed = [k for k, v in self._all(copy(f"m{i:02d}", src.replace(old, new))).items()
                              if v]
                    self.assertTrue(failed, "壊しても、 どの場面も落ちません")


class SyntheticValuesTest(unittest.TestCase):
    """架空の値が、 公開のリポジトリに置ける形であること。"""

    def test_the_name_check_does_not_take_them_for_site_values(self) -> None:
        """名前の検査（T0-01）は、 架空の値を現場の値として拾わない。"""
        body = "\n".join(NOT_SHOWN) + "\n"
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "zz_values.md"
            p.write_text(body, encoding="utf-8")
            orig, C.ROOT = C.ROOT, Path(td)
            allow, C._ALLOW_CACHE = C._ALLOW_CACHE, {}
            try:
                hits = C.check(p, ())
            finally:
                C.ROOT, C._ALLOW_CACHE = orig, allow
        self.assertEqual(hits, [])

    def test_documentation_networks_are_not_generic(self) -> None:
        """**検査は文書用の網を除かない。** 除くと、 B の宛先の場面が何も確かめない。"""
        for ip in (A_IP, B_IPS[0], B_PLC_HOST):
            self.assertFalse(I._generic_ip(ip))
        for ip in ("127.0.0.1", "0.0.0.0", "255.255.255.0"):
            self.assertTrue(I._generic_ip(ip))


class ItDoesNotRunTheTargetTest(unittest.TestCase):
    """**PR の中身を実行しない。** 使う git の命令は、 一覧と読み出しだけ。"""

    def test_only_reading_git_commands(self) -> None:
        src = CHECKER.read_text(encoding="utf-8")
        used = set(re.findall(r'\["git", "([a-z-]+)"', src))
        used |= set(re.findall(r'_git\(root, "([a-z-]+)"', src))
        self.assertTrue(used)
        self.assertLessEqual(used, {"ls-files", "cat-file"})
        for word in ("exec(", "eval(", "import_module", "runpy", "shell=True", "os.system"):
            self.assertNotIn(word, src)


def _workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


class WorkflowTest(unittest.TestCase):
    """共通の CI に **任意の入力** で入れる。 既定は false で、 今の呼び出し側は変わらない。"""

    STEP_RUN = "validators/scripts/ci/customer_isolation_check.py"

    def setUp(self) -> None:
        self.wf = _workflow("basic-checks-reusable.yml")
        self.steps = self.wf["jobs"]["checks"]["steps"]

    def test_the_input_is_off_by_default(self) -> None:
        on = self.wf.get("on", self.wf.get(True))
        self.assertEqual(set(on), {"workflow_call"})
        inp = on["workflow_call"]["inputs"]["run-isolation-check"]
        self.assertEqual((inp["type"], inp["required"], inp["default"]),
                         ("boolean", False, False))

    def test_only_the_new_step_uses_the_input(self) -> None:
        mine = [s for s in self.steps if self.STEP_RUN in s.get("run", "")]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0].get("if"), "${{ inputs.run-isolation-check }}")
        others = [s for s in self.steps if s is not mine[0]]
        self.assertFalse([s for s in others if "run-isolation-check" in json.dumps(s)])
        self.assertFalse([s for s in others if "customer_isolation" in json.dumps(s)])

    def test_it_reads_only_and_gets_no_secret(self) -> None:
        self.assertEqual(self.wf.get("permissions"), {"contents": "read"})
        text = (WORKFLOWS / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        for word in ("secrets.", "pull_request_target", "workflow_run", C.REGISTRY_ENV):
            self.assertNotIn(word, text)
        step = next(s for s in self.steps if self.STEP_RUN in s.get("run", ""))
        # 基準の commit は環境変数で渡す（式を run の中に書かない）
        self.assertNotIn("${{", step["run"])
        self.assertEqual(step["env"]["BASE_SHA"], "${{ github.event.pull_request.base.sha }}")

    def test_this_repository_runs_the_tests(self) -> None:
        own = _workflow("basic-checks.yml")["jobs"]["checks"]["steps"]
        runs = [s.get("run", "") for s in own]
        self.assertIn("python scripts/ci/test_customer_isolation.py", runs)
        pip = next(i for i, r in enumerate(runs) if "pip install" in r and "pyyaml" in r)
        self.assertLess(pip, runs.index("python scripts/ci/test_customer_isolation.py"))


class ReadmeExampleTest(unittest.TestCase):
    """README の宣言の例が、 今の決まりで読めること（例が古びない）。"""

    def test_the_example_profile_is_valid(self) -> None:
        text = README.read_text(encoding="utf-8")
        m = re.search(r"<!-- customer-profile\.yaml の例 -->\s*```yaml\n(.*?)```", text, re.S)
        self.assertIsNotNone(m, "README に宣言の例がありません")
        prof = I.parse_profile(m.group(1), yaml)
        self.assertTrue(prof["equipment.cameras"])
        self.assertTrue(prof["endpoints.network"], "網の書き方の例がありません")


if __name__ == "__main__":
    unittest.main()

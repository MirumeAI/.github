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
  ・製造番号・番地・構成の名前・検査の性質は `EXAMPLE`・`example` を含む形。
    トリガと面は `trig_example_` で始まる形。 PLC の表の向きや信号の名前も架空
    （検査は形で読むので、 Core の名前を写さない）

どの場面でも **出力に値が出ないこと** を見る。 最後に、 検査の守りを 1 つずつ
壊して、 どれかの場面が落ちることを確かめる（変異の試験。 撮像側と PDM の
`scripts/ci/mutation_check.py` と同じ決まり: 置換前が 1 か所だけ見つからなければ
失敗、 壊す前に全部の場面が通ることを先に確かめる）。

PyYAML が要る。 CI（環境変数 CI がある）で無ければ、 飛ばさずに落とす。
"""
from __future__ import annotations

import ast
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
import time
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
A_FLOW = "example-flow-" + "a"
A_IP = _ip(0, 10)
A_SERIAL = "EXAMPLE-SN-" + "0001"
A_TRIGGER = "trig_example_" + "a_top"

#: 案件 B（PLC あり・カメラ 5 台）。 面を分けて撮る（`_1`・`_2`）面を持つ。
B_PROFILE = "example-" + "b"
B_FLOW = "example-flow-" + "b"
B_IPS = [_ip(1, 20 + i) for i in range(5)]
B_SERIALS = ["EXAMPLE-SN-" + f"{100 + i:04d}" for i in range(5)]
B_PLC_HOST = _ip(2, 5)
B_PLC_PORT = 5000 + 10
B_TRIGGERS = ["trig_example_" + s for s in ("b_front_1", "b_front_2", "b_side", "b_back")]
B_GROUP = "group_example_" + "1"
#: 混ざった構成のファイルの名前（B の構成の名前を含む）。
B_FILE = "config." + B_PROFILE + ".json"

#: 宣言に書いてはいけない値と、 鍵に書き誤った数（試験の中だけで組み立てる）。
CREDENTIAL = "example-" + "not-a-real-password"
TOKEN = "ghp" + "_" + "Ab3" * 12
NUMBER_KEY = "86420" + "24680"
HEX_KEY, HEX_VALUE = "0x7531" + "eca8642", str(0x7531eca8642)
NUMBER_SERIAL = "12345" + "67890"
FLOAT_SERIAL = "13579" + "13579"


def b_plc(enabled: bool = True) -> dict:
    """B の PLC の表。 向き・信号・群の名前は架空（形だけ Core と同じ）。"""
    n = iter(range(100, 1000))

    def sig() -> dict:
        return {"bit": 0, "data": "EXAMPLE_ADDR_" + str(next(n))}

    return {
        "enabled": enabled,
        "address": {"host": B_PLC_HOST, "port": B_PLC_PORT},
        "to_pc_example": {
            "shot_request": {t: {"trig_bit": sig()} for t in B_TRIGGERS},
            "slots_example": {B_GROUP: {"device": sig(), "triggers": list(B_TRIGGERS)}},
        },
        "to_plc_example": {
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

#: S5 の場面の値（記録の中の、 宣言に無い IP）。
S5_IPS = tuple(_ip(n, h) for n, h in ((1, 77), (2, 9), (1, 78), (1, 79), (1, 81), (2, 10)))
#: 4 つの数の package の版（鍵 version の値なら版、 ほかの鍵の値なら IP とみなす）。 _ip と同じく、
#: 実行するときに組み立てる（ソースに IPv4 の形を置かない）。
VERSION4 = ".".join(("9", "1", "0", "70"))

#: 出力に出てはいけない値（どの場面でも見る）。
NOT_SHOWN = (A_PROFILE, A_FLOW, A_IP, A_SERIAL, A_TRIGGER, B_PROFILE, B_FLOW, *B_IPS,
             *B_SERIALS, B_PLC_HOST, *B_TRIGGERS, B_GROUP, *B_ADDRS, CREDENTIAL, TOKEN,
             NUMBER_KEY, HEX_KEY, HEX_VALUE, NUMBER_SERIAL, FLOAT_SERIAL, *S5_IPS, VERSION4)


def a_config() -> dict:
    return {
        "_profile": A_PROFILE,
        "flow": A_FLOW,
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
        "project": {"flow": A_FLOW, "profiles": [A_PROFILE]},
        "capabilities": {"plc": False, "nas": False, "ai": True},
        "equipment": {"cameras": [{"id": "camera1", "ip": A_IP, "serial": A_SERIAL}]},
        "triggers": [A_TRIGGER],
        "faces": [A_TRIGGER],
    }


def b_config() -> dict:
    return {
        "_profile": B_PROFILE,
        "flow": B_FLOW,
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
        "project": {"flow": B_FLOW, "profiles": [B_PROFILE]},
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
        IA + "pdm_guard_policy.json": _json({"example_local": ["127.0.0.1"]}),
        "config/pdm/capture/.gitkeep": "",
        "config/pdm/bbox_acquire/" + A_TRIGGER + ".json": _json({"bbox": [0, 0, 10, 10]}),
        SERVICE: ("# 案件の設定は config/image-acquisition/config.json\n"
                  "ExecStart=/usr/bin/true --host 0.0.0.0\n"),
        "versions.yaml": "schema_version: 1\n",
    }


def b_files(config: "dict | None" = None) -> dict:
    """案件 B のリポジトリ（PLC あり）。 面ごとの設定は基の名前でも持つ。"""
    front = B_TRIGGERS[0].rsplit("_", 1)[0]          # 基の名前（末尾の _<数字> を落とす）
    return {
        "customer-profile.yaml": _yaml(b_profile()),
        IA + "config.json": _json(config or b_config()),
        "config/pdm/bbox_acquire/" + front + ".json": _json({"bbox": [1, 2, 3, 4]}),
        "config/pdm/adaptive_mask/" + front + "__region1.json":
            _json({"of": B_TRIGGERS[0], "tag": front + "__region1"}),
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

    def __init__(self, name: str, commits: list, exit: int, codes=(), exact: bool = True,
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

    def a_with(**changes) -> dict:
        return _with(a_files(), **changes)

    # ---- 指示の 1〜13（起きたことの再現と、 止める場面）
    add("1. 案件 A の正しい設定（一般の値を含む）", a_files(), 0)
    add("2. A に B の構成のファイルをまるごと足す", a_with(**{
        "config__image-acquisition__" + B_FILE: _json(b_config())}), 1,
        {"S1-file", "S2-plc", "S2-nas", "S3-profile", "S3-flow", "S3-camera", "S3-face",
         "L1-endpoint", "L1-serial", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    add("2b. 値の無い知らない設定のファイルも黙って通さない", a_with(**{
        "config__image-acquisition__settings.json": _json({"memo": "x"})}), 1, {"S1-file"})
    add("3. PLC を使わない A に B の PLC の表（enabled は false）",
        a_files(config=a_cfg(plc=b_plc(False))), 1,
        {"S2-plc", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    cfg = a_config()
    cfg["camera"]["camera1"]["ip_address"] = B_IPS[0]
    add("4. A のカメラの宛先を B のものにする", a_files(config=cfg), 1, {"L1-endpoint"})
    cfg = a_config()
    cfg["shot"]["camera1"]["triggers"].append(B_TRIGGERS[2])
    add("5. A のトリガに B のものを足す", a_files(config=cfg), 1, {"L1-trigger"})
    add("6. A の構成の名前を B のものにする", a_files(config=a_cfg(_profile=B_PROFILE)), 1,
        {"S3-profile"})
    add("7. deploy が、 無い設定のファイルに触れる（道）", a_with(**{
        "deploy__image-acquisition__example.service":
            f"# 案件の設定は config/image-acquisition/{B_FILE}\n"}), 1, {"S4-missing"})
    add("7b. deploy が、 許されていない設定のファイルに触れる", a_with(**{
        "config__image-acquisition__settings.json": _json({"memo": "x"}),
        "deploy__image-acquisition__example.service":
            "# 設定は config/image-acquisition/settings.json\n"}), 1,
        {"S1-file", "S4-not-allowed"})
    add("7c. versions.yaml が、 無い設定のファイルに触れる（名前）", a_with(**{
        "versions.yaml": f"schema_version: 1\n# {B_FILE} を読む\n"}), 1, {"S4-missing"})
    add("7d. 配置先の絶対の道は、 この Repository の参照とみなさない", a_with(**{
        "deploy__image-acquisition__example.service":
            "ExecStart=/opt/example/config/example.json\n"}), 0)
    add("7f. ./ と ../ で始まる、 この Repository の中の参照も照らす", a_with(**{
        "deploy__image-acquisition__example.service":
            f"ExecStart=/usr/bin/true --c ./config/image-acquisition/{B_FILE}\n"
            f"ExecStartPre=/bin/cp ../../config/image-acquisition/{B_FILE} /tmp/x\n"}), 1,
        {"S4-missing"})
    add("7g. deploy そのものがリンク", _with(a_files(), **{
        "deploy__image-acquisition__example.service": None, "deploy": Link("config")}), 1,
        {"S4-link"})
    add("7e. deploy の中のリンク", a_with(**{
        "deploy__image-acquisition__link.service": Link("../../versions.yaml")}), 1,
        {"S4-link"})
    add("8. 宣言が無い", a_with(**{"customer-profile.yaml": None}), 2, error="profile-missing")
    add("9. 宣言が YAML として読めない", a_with(**{
        "customer-profile.yaml": "schema_version: [1\n"}), 2, error="profile-yaml")
    # 生の制御文字（safe_dump は \a の書き方に直すので、 文字で足す）
    bel = _yaml(a_profile()) + "# example" + chr(7) + "\n"
    add("9b. 宣言に使えない文字（制御文字）", a_with(**{"customer-profile.yaml": bel}), 2,
        error="profile-yaml")
    add("10. 資格情報らしい鍵", a_with(**{
        "customer-profile.yaml": _yaml(a_profile()) + f"password: {CREDENTIAL}\n"}), 2,
        error="profile-credential")
    add("10b. 資格情報らしい値", a_files(profile=a_prof(extensions=[TOKEN])), 2,
        error="profile-credential")
    add("10c. 数の鍵（10 進）を、 誤りの文に出さない", a_with(**{
        "customer-profile.yaml": _yaml(a_profile()) + f"{NUMBER_KEY}: x\n"}), 2,
        error="profile-yaml")
    cams = (f"equipment:\n  cameras:\n  - id: camera1\n    ip: {A_IP}\n"
            f"    serial: {A_SERIAL}\n    {HEX_KEY}: 1\n")
    no_eq = {k: v for k, v in a_profile().items() if k != "equipment"}
    add("10d. 数の鍵（16 進・カメラの項目の中）を、 誤りの文に出さない", a_with(**{
        "customer-profile.yaml": _yaml(no_eq) + cams}), 2, error="profile-yaml")
    add("11. B の正しい設定（PLC の表・Core の鍵・基の名前の面のファイル）", b_files(), 0)
    # 12 は、 どの場面でも見る（出力に値が出ない）。
    grown = a_prof(endpoints=[_ip(0, 99)],
                   triggers=[A_TRIGGER] + ["trig_example_" + x for x in ("n1", "n2", "n3")])
    add("13. 宣言の増え方は、 種類と数だけを出す", [
        a_files(profile=a_prof(triggers=[A_TRIGGER, "trig_example_old"])),
        a_files(profile=grown)], 0, args=("--base-ref", "{base}"),
        texts=("PROFILE EXPANSION DETECTED", "endpoints: +1 / -0", "triggers: +3 / -1"))

    # ---- 層 1 の補い（決まった場所の外）と、 PLC の表の形
    add("11b. 補い: 決まった場所の外の、 別の案件のトリガ（JSON の鍵）", a_with(**{
        "config__image-acquisition__recipe.json": _json({"faces": {B_TRIGGERS[1]: {"n": 1}}})}),
        1, {"L1-name"})
    add("11c. 補い: 決まった場所の外の、 別の案件の宛先（JSON の値）", a_with(**{
        "config__image-acquisition__machine.json": _json({"nas_host": B_IPS[1]})}), 1,
        {"L1-endpoint"})
    add("11d. 補い: .txt の中の、 別の案件のトリガ", a_with(**{
        "config__pdm__align_reference.txt": B_TRIGGERS[2] + "\n"}), 1, {"L1-name"})
    add("11e. 補い: JSON の鍵の中の IP", a_with(**{
        "config__image-acquisition__machine.json": _json({B_IPS[1]: "x"})}), 1,
        {"L1-endpoint"})
    add("11f. 補い: NUL を含む .txt も読む", a_with(**{
        "config__pdm__align_reference.txt": "EXAMPLE" + chr(0) + B_TRIGGERS[2] + "\n"}), 1,
        {"L1-name"})
    add("11g. 補い: <名>__<別の名前> は、 前半だけで通さない", a_with(**{
        "config__image-acquisition__machine.json":
            _json({"x": A_TRIGGER + "__" + B_TRIGGERS[0]})}), 1, {"L1-name"})
    bc = b_config()
    bc["plc"]["to_pc_example"]["shot_request"][B_TRIGGERS[0]]["trig_example_signal"] = {
        "bit": 1, "data": B_ADDRS[0]}
    add("11h. トリガの項目の中の、 Core の一覧に無い trig_ の鍵も照らす",
        b_files(config=bc), 1, {"L1-name"})
    bc = b_config()
    bc["plc"]["to_pc_example"]["shot_request"]["trig_example_b_extra"] = {
        "trig_bit": {"bit": 0, "data": B_ADDRS[0]}}
    add("11i. 宣言に無い shot_request の項目（トリガ）", b_files(config=bc), 1, {"L1-trigger"})
    bc = b_config()
    bc["plc"]["address"]["backup_host"] = _ip(2, 99)
    add("11j. plc.address の host・port の外の鍵も照らす", b_files(config=bc), 1,
        {"L1-endpoint"})
    bc = b_config()
    bc["plc"]["address"] = {"ip": B_PLC_HOST}
    add("11k. plc.address が host と port の形でない", b_files(config=bc), 1,
        {"L1-plc-endpoint"})
    cfg = a_config()
    cfg["camera"]["camera1"]["serial_number"] = B_SERIALS[0]
    add("11l. 製造番号を B のものにする", a_files(config=cfg), 1, {"L1-serial"})
    cfg = a_config()
    cfg["camera"]["camera1"]["serial_number"] = float(FLOAT_SERIAL)
    add("11m. 小数で書いた製造番号も照らす", a_files(config=cfg), 1, {"L1-serial"})

    # ---- 宣言の増え方（そのほか）
    add("13b. 宣言が新しく作られた", [a_with(**{"customer-profile.yaml": None}), a_files()], 0,
        args=("--base-ref", "{base}"), texts=("この変更で作られました", "PROFILE EXPANSION DETECTED"))
    add("13c. 宣言が変わらない", [a_files(), a_with(**{"config__README.md": "# x\n"})], 0,
        args=("--base-ref", "{base}"), texts=("変化なし",), absent=("PROFILE EXPANSION DETECTED",))
    add("13d. 基準の commit が手元に無い", a_files(), 0, args=("--base-ref", "0" * 40),
        texts=("比べていません",))
    add("13e. --base-ref の形が違う", a_files(), 2, error="usage", args=("--base-ref", "main"))
    add("13f. 減っただけでは、 増えたと言わない", [
        a_files(profile=a_prof(triggers=[A_TRIGGER, "trig_example_old"])), a_files()], 0,
        args=("--base-ref", "{base}"), texts=("triggers: +0 / -1",),
        absent=("PROFILE EXPANSION DETECTED",))
    add("13g. 能力が false から true になったら、 増えたと言う", [
        a_files(), a_files(profile=a_prof(capabilities={"plc": False, "nas": True, "ai": True}),
                           config=a_cfg(nas={"enabled": True}))], 0,
        args=("--base-ref", "{base}"),
        texts=("capabilities.nas: false → true", "PROFILE EXPANSION DETECTED"))
    other = "example-flow-" + "x"
    add("13h. 検査の性質が変わったら、 増えたと言う", [
        a_files(), a_files(profile=a_prof(project={"flow": other, "profiles": [A_PROFILE]}),
                           config=a_cfg(flow=other))], 0,
        args=("--base-ref", "{base}"), texts=("変わりました", "PROFILE EXPANSION DETECTED"))
    add("13i. 基準の宣言が読めない（使えない文字）なら、 比べないだけ", [
        a_with(**{"customer-profile.yaml": bel}), a_files()], 0,
        args=("--base-ref", "{base}"), texts=("比べていません",))

    # ---- 宛先の決まり（ちょうどの値が原則。 網は理由つきで、 狭いものだけ）
    p = a_profile()
    p["equipment"]["cameras"][0].pop("ip")
    net = {"network": _ip(0, 0) + "/28", "reason": "架空の案件の、 カメラの網"}
    in_net = dict(p, endpoints=[net])
    add("網で許す（理由つき・/28）", a_files(profile=in_net), 0)
    cfg = a_config()
    cfg["camera"]["camera1"]["ip_address"] = _ip(0, 20)
    add("宣言した網の外の IP", a_files(profile=in_net, config=cfg), 1, {"L1-endpoint"})
    add("網の値: 宣言の網の中（/28）は通す", _with(a_files(profile=in_net), **{
        "config__image-acquisition__machine.json": _json({"allow": _ip(0, 0) + "/28"})}), 0)
    add("網の値: 宣言の網より広い（/24）は通さない", _with(a_files(profile=in_net), **{
        "config__image-acquisition__machine.json": _json({"allow": _ip(0, 0) + "/24"})}), 1,
        {"L1-endpoint"})
    add("端末の書き方（IP/24）と URL の道は、 その IP として照らす", a_with(**{
        "config__image-acquisition__machine.json":
            _json({"if": A_IP + "/24", "url": "rtsp://" + A_IP + "/1/stream"})}), 0)
    add("広すぎる網は断る", a_files(profile=dict(p, endpoints=[
        {"network": "192.0.0.0/16", "reason": "広すぎる"}])), 2, error="profile-schema")
    # 境: /24 は許し、 /23 は断る（#115）
    add("/24 の網は許す（境）", a_files(profile=dict(p, endpoints=[
        {"network": _ip(0, 0) + "/24", "reason": "架空の案件の、 カメラの網（境）"}])), 0)
    add("/23 の網は断る（境）", a_files(profile=dict(p, endpoints=[
        {"network": _ip(0, 0) + "/23", "reason": "広すぎる（境）"}])), 2, error="profile-schema",
        texts=("/24 か、それより狭く",))
    add("網に理由が無い", a_files(profile=dict(p, endpoints=[{"network": net["network"]}])), 2,
        error="profile-schema")
    add("網を文字で書く", a_files(profile=dict(p, endpoints=[net["network"]])), 2,
        error="profile-schema")
    many = [{"network": _ip(k // 16, (k % 16) * 16) + "/28", "reason": "架空"} for k in range(33)]
    add("網が多すぎる", a_files(profile=dict(p, endpoints=many)), 2, error="profile-schema")

    # ---- S1（ファイル）
    add("defaults/ に案件の値を置く", a_with(**{"config__pdm__defaults__x.json": _json({})}), 1,
        {"S1-defaults"})
    add("defaults/ の空の .gitkeep と説明の README.md は通す", a_with(**{
        "config__pdm__defaults__.gitkeep": "", "config__pdm__defaults__README.md": "# x\n"}), 0)
    add("align_reference.txt は PDM の置き場の根だけ", a_with(**{
        "config__pdm__capture__align_reference.txt": "EXAMPLE-LOT-0001\n"}), 1, {"S1-file"})
    add("defaults/ は宣言しても許さない", a_files(profile=a_prof(config_files={"extra": [
        {"path": "config/pdm/defaults/x.json", "reason": "架空"}]})), 2, error="profile-schema")
    add("config/ の下のリンク", a_with(**{
        "config__image-acquisition__link.json": Link("../../versions.yaml")}), 1, {"S1-link"})
    add("中身のある .gitkeep", a_with(**{"config__pdm__capture__.gitkeep": "x\n"}), 1,
        {"S1-file"})
    # 面ごとの置き場の中の、 . で始まるもの（配る道具が黙って飛ばす。 名前も面でない）
    add("config/pdm の . で始まる部品", a_with(**{
        "config__pdm__bbox_acquire__.hidden.json": _json({})}), 1, {"S1-file", "S3-face-file"})
    add("config/pdm の .json・.txt 以外", a_with(**{
        "config__pdm__bbox_acquire__" + A_TRIGGER + ".yaml": "x: 1\n"}), 1, {"S1-file"})
    add("config/pdm の知らない名前", a_with(**{"config__pdm__unknown_tool.json": _json({})}),
        1, {"S1-file"})
    add("決まった置き場の外（config/ の直下）", a_with(**{"config__x.json": _json({})}), 1,
        {"S1-file"})
    add("撮像側の置き場の下のフォルダ", a_with(**{
        "config__image-acquisition__sub__config.json": _json(a_config())}), 1, {"S1-file"})
    extra = {"config_files": {"extra": [{"path": IA + "extra.json", "reason": "架空の追加"}]}}
    add("宣言した追加のファイル", _with(a_files(profile=a_prof(**extra)), **{
        "config__image-acquisition__extra.json": _json({"memo": "x"})}), 0)
    add("宣言した追加のファイルが無い（注意だけ）", a_files(profile=a_prof(**extra)), 0,
        texts=("宣言して使っていないもの",))
    add("宣言の extra の道の形（..）", a_files(profile=a_prof(config_files={"extra": [
        {"path": IA + "../x.json", "reason": "架空"}]})), 2, error="profile-schema")
    add("宣言の extra の道の形（//）", a_files(profile=a_prof(config_files={"extra": [
        {"path": IA + "/x.json", "reason": "架空"}]})), 2, error="profile-schema")

    # ---- S2・S3
    add("宣言に無い面の名前のファイル（config/pdm）", a_with(**{
        "config__pdm__region_split__" + B_TRIGGERS[2] + ".json": _json({})}), 1, {"S3-face-file"})
    add("宣言に無い面の名前のファイル（撮像側の置き場）", a_with(**{
        "config__image-acquisition__" + B_TRIGGERS[3] + ".json": _json({})}), 1,
        {"S1-file", "L1-name"})
    cfg = a_config()
    cfg["face_dict"][B_TRIGGERS[3]] = "面"
    add("宣言に無い面（face_dict）", a_files(config=cfg), 1, {"S3-face"})
    cfg = a_config()
    cfg["camera"]["camera2"] = {"camera_type": "area"}
    add("宣言に無いカメラ", a_files(config=cfg), 1, {"S3-camera"})
    add("flow が違う", a_files(config=a_cfg(flow=B_FLOW)), 1, {"S3-flow"})
    axes = {"axis_example": "value_example", "switch_example": True}
    ins_cfg = {k: v for k, v in a_config().items() if k != "flow"}
    ins_cfg["inspection"] = dict(axes)
    ins_prof = a_prof(project={"inspection": dict(axes), "profiles": [A_PROFILE]})
    add("inspection で書いた設定（宣言も inspection）", a_files(profile=ins_prof, config=ins_cfg), 0)
    add("inspection が宣言と違う", a_files(profile=ins_prof, config=dict(
        ins_cfg, inspection=dict(axes, switch_example=False))), 1, {"S3-flow"})
    add("flow と inspection の両方を書いた設定", a_files(config=dict(a_config(), inspection=axes)),
        2, error="config-field")
    add("flow も inspection も無い設定", a_files(config={
        k: v for k, v in a_config().items() if k != "flow"}), 2, error="config-field")
    add("宣言に flow と inspection の両方", a_files(profile=a_prof(
        project={"flow": A_FLOW, "inspection": axes, "profiles": [A_PROFILE]})), 2,
        error="profile-schema")
    add("NAS の有無が宣言と違う", a_files(profile=a_prof(
        capabilities={"plc": False, "nas": True, "ai": True})), 1, {"S2-nas"})
    add("AI の有無が宣言と違う", a_files(profile=a_prof(
        capabilities={"plc": False, "nas": False, "ai": False})), 1, {"S2-ai"})
    add("nas.enabled を書いていない（照らさずに、 そう言う）", a_files(config=a_cfg(nas={})), 0,
        texts=("照らしていません",))
    add("nas の節が無い", a_files(config={k: v for k, v in a_config().items() if k != "nas"}),
        2, error="config-field")
    add("ai.enabled を書いていない（Core も止める形）", a_files(config=a_cfg(ai={})), 2,
        error="config-field")
    bc = b_config()
    bc["plc"]["enabled"] = False
    add("PLC を使う宣言なのに plc.enabled が false", b_files(config=bc), 1, {"S2-plc-enabled"})
    add("PLC を使わない宣言に PLC の宛先", a_files(profile=a_prof(
        plc={"endpoints": [f"{B_PLC_HOST}:{B_PLC_PORT}"]})), 2, error="profile-schema")
    add("主の設定が無い", a_with(**{"config__image-acquisition__config.json": None}), 2,
        error="config-missing")
    add("主の設定の JSON が壊れている", a_with(**{
        "config__image-acquisition__config.json": "{\n"}), 2, error="config-json")
    add("名前に値を含む壊れた JSON でも、 道を伏せる", a_with(**{
        "config__image-acquisition__" + B_FILE: "{\n"}), 2, error="config-json")
    hidden = _json(a_config()).replace(
        '"plc": {', '"plc": ' + json.dumps(b_plc(False)) + ',\n  "plc": {', 1)
    add("設定の JSON の同じ鍵（PLC の表を、 後の enabled: false で隠す）", a_with(**{
        "config__image-acquisition__config.json": hidden}), 2, error="config-json",
        texts=("同じ鍵が 2 回あります（",))
    add("PDM の置き場に置いた撮像側の構成（_profile・plc を持つ）も照らす", a_with(**{
        "config__pdm__bbox_acquire__" + A_TRIGGER + ".json": _json({
            "_profile": B_PROFILE,
            "plc": {"enabled": False, "address": {"host": "example-host", "port": 1}}})}), 1,
        {"S2-plc", "S3-profile", "L1-plc-endpoint"})
    add("PDM の置き場に置いた PLC の表（IP も trig_ の語も無い）も照らす", a_with(**{
        "config__pdm__bbox_acquire__" + A_TRIGGER + ".json": _json({"plc": {
            "enabled": False, "address": {"host": "example-host", "port": 1},
            "to_pc_example": {"shot_request": {"example_trigger_b": {
                "sig_example": {"bit": 0, "data": "EXAMPLE_ADDR_900"}}}}}})}), 1,
        {"S2-plc", "L1-plc-endpoint", "L1-trigger", "L1-plc-address"})
    add("PDM の JSON の camera の節は、 撮像側の設定とみなさない", a_with(**{
        "config__pdm__bbox_acquire__" + A_TRIGGER + ".json":
            _json({"camera": {"cam_example": {}}})}), 0)

    # ---- 参考（不合格にしない）と、 報告だけ
    add("deploy/ の中の値は参考だけ（不合格にしない）", a_with(**{
        "deploy__image-acquisition__example.service": f"Environment=HOST={B_IPS[0]}\n"}), 0,
        texts=("参考（強制しません",))
    add("説明の .md の中の値は参考だけ（不合格にしない）", a_with(**{
        "config__image-acquisition__notes.md": f"{B_IPS[0]} と {B_TRIGGERS[0]}\n"}), 0,
        texts=("参考（強制しません",))
    cfg = a_config()
    cfg["shot"]["camera1"]["triggers"].append(B_TRIGGERS[2])
    add("--report-only は 0 で終える（所見は出す）", a_files(config=cfg), 0, {"L1-trigger"},
        args=("--report-only",), texts=("REPORT ONLY",))

    # ---- 宣言の形
    prof_text = _yaml(a_profile())
    add("同じ鍵が 2 回（宣言）", a_with(**{
        "customer-profile.yaml": prof_text + f"triggers: [{B_TRIGGERS[0]}]\n"}), 2,
        error="profile-yaml")
    add("知らない鍵（鍵の名前も出さない）", a_with(**{
        "customer-profile.yaml": prof_text + f"{B_TRIGGERS[1]}: 1\n"}), 2,
        error="profile-schema")
    no_faces = {k: v for k, v in a_profile().items() if k not in ("triggers", "faces")}
    add("別名（*）は使わない", a_with(**{
        "customer-profile.yaml": _yaml(no_faces)
        + f"triggers: &t [{A_TRIGGER}]\nfaces: *t\n"}), 2, error="profile-yaml")
    num = a_profile()
    num["equipment"]["cameras"][0]["serial"] = int(NUMBER_SERIAL)
    add("数で書いた製造番号は、 引用符で囲むよう言う", a_files(profile=num), 2,
        error="profile-schema", texts=("引用符",))
    add("真偽で書いた値も、 引用符で囲むよう言う", a_files(profile=a_prof(triggers=[True])), 2,
        error="profile-schema", texts=("引用符",))
    add("宣言の inspection が対応表でない", a_files(profile=a_prof(
        project={"inspection": "example", "profiles": [A_PROFILE]})), 2, error="profile-schema")

    # ---- S5: 記録（deployment/・validation/。 P1-05）。 記録には値を書かない
    rec = ("schema_version: 2\nrecord_id: M01-0001\nmachine: M01\nsequence: 1\n"
           "status: LEGACY\nobserved: {note: loopback 127.0.0.1}\n")
    add("S5a. 記録に一般の値と記号だけなら通る", a_with(**{
        "deployment__M01-0001.yaml": rec, "deployment__README.md": "# d\n",
        "deployment__.gitkeep": "",
        "validation__VR-PJ001-2026-001.yaml": "cases: [{id: C1, result: PASS}]\n"}), 0)
    add("S5b. 配置の記録の中の宣言に無い IP は不合格", a_with(**{
        "deployment__M01-0001.yaml": rec + f"peer: '{_ip(1, 77)}'\n"}), 1, {"S5-endpoint"})
    add("S5c. 検証の記録の中の宣言に無い trig_ の語は不合格", a_with(**{
        "validation__VR-PJ001-2026-001.yaml": f"cases: [{{id: C1, note: {B_TRIGGERS[0]}}}]\n"}), 1,
        {"S5-name"})
    add("S5d. 証拠のファイルの中の宣言に無い IP も不合格", a_with(**{
        "validation__evidence__run.txt": f"connected to {_ip(2, 9)}\n"}), 1, {"S5-endpoint"})
    add("S5e. 記録の dir の説明の .md の中の値は参考だけ", a_with(**{
        "validation__README.md": f"例 {_ip(1, 78)}\n"}), 0, texts=("参考",))
    add("S5f. 記録の dir の下のリンクは読まずに断る", a_with(**{
        "deployment__M01-0001.yaml": Link("../versions.yaml")}), 1, {"S5-link"})
    add("S5g. 検証の記録の案件の記号が System Release の記号と違う", a_with(**{
        "validation__VR-PJ002-2026-001.yaml": "x: 1\n",
        "release__PJ001-R2026.10.1.yaml": "x: 1\n"}), 1, {"S5-project-code"})
    add("S5h. 検証の記録どうしで案件の記号が違う", a_with(**{
        "validation__VR-PJ001-2026-001.yaml": "x: 1\n",
        "validation__VR-PJ002-2026-002.yaml": "x: 1\n"}), 1, {"S5-project-code"})
    add("S5i. 案件の記号がそろっていれば通る", a_with(**{
        "validation__VR-PJ001-2026-001.yaml": "x: 1\n",
        "validation__VR-PJ001-2026-002.yaml": "x: 1\n",
        "release__PJ001-R2026.10.1.yaml": "x: 1\n"}), 0)
    add("S5j. 手順の .md（validation/protocols/）の中の宣言に無い IP は不合格", a_with(**{
        "validation__protocols__README.md": "# p\n",
        "validation__protocols__VP-01.md": f"手順 {_ip(1, 79)}\n"}), 1, {"S5-endpoint"})
    add("S5k. 証拠の .md の中の宣言に無い trig_ の語は不合格", a_with(**{
        "validation__evidence__run.md": f"{B_TRIGGERS[0]}\n"}), 1, {"S5-name"})
    add("S5l. 鍵 version の値の 4 つの数の版は IP とみなさない（棚卸しの package）", a_with(**{
        "deployment__M01-0001.yaml": rec + "packages:\n"
        f'  - {{ name: "a", version: "{VERSION4}", sha256: null, editable: false }}\n'
        f"  - {{name: b, version: {VERSION4}}}\n"
        f'  - {{"name": "c", "version": "{VERSION4}"}}\n'}), 0)
    add("S5m. 鍵 version の外の 4 つの数は IP とみなす", a_with(**{
        "deployment__M01-0001.yaml": rec + f"revision: '{VERSION4}'\n"}), 1, {"S5-endpoint"})
    add("S5n. 版と同じ行の宣言に無い IP は不合格", a_with(**{
        "deployment__M01-0001.yaml": rec + "packages:\n"
        f'  - {{ name: "a", version: "{VERSION4}", note: "{_ip(1, 81)}" }}\n'}), 1,
        {"S5-endpoint"})
    add("S5o. 中身のある .gitkeep も照らす", a_with(**{
        "validation__evidence__.gitkeep": f"{_ip(2, 10)}\n"}), 1, {"S5-endpoint"})
    add("S5p. 記録の dir そのもののリンクは読まずに断る", a_with(**{
        "deployment": Link("config")}), 1, {"S5-link"})
    add("S5q. release/ に案件の記号が 2 つあれば、 検証の記録を出す", a_with(**{
        "validation__VR-PJ001-2026-001.yaml": "x: 1\n",
        "release__PJ001-R2026.10.1.yaml": "x: 1\n",
        "release__PJ002-R2026.10.1.yaml": "x: 1\n"}), 1, {"S5-project-code"})
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


def _write(root: Path, snap: dict) -> None:
    for rel, body in snap.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, Link):
            os.symlink(body.target, p)
        else:
            p.write_text(body, encoding="utf-8")


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
        _write(root, snap)
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
        root = Path(base) / f"s{i:03d}"
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
    saved = {k: os.environ.pop(k, None) for k in ("CI_ROOT", "GITHUB_ACTIONS")}
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                code = mod.main(["--root", str(root), *args])
            except SystemExit as e:
                code = e.code
    finally:
        os.environ.update({k: v for k, v in saved.items() if v is not None})
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
    if "Traceback" in out or "ERROR [internal]" in out:
        p.append("予期しない誤りで止まりました")
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

    def test_the_expansion_is_also_a_warning_in_actions(self) -> None:
        """宣言が増えたら、 Actions では決まった文の注意（::warning::）も出す。 値は入れない。"""
        sc = next(s for s in SCENARIOS if s.name.startswith("13. "))
        root, shas = REPOS[sc.name]
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = I.main(["--root", str(root), "--base-ref", shas[0]])
        warn = [ln for ln in buf.getvalue().splitlines() if ln.startswith("::")]
        self.assertEqual(code, 0)
        self.assertEqual(len(warn), 1)
        self.assertTrue(warn[0].startswith("::warning title=customer-profile.yaml::PROFILE EXPANSION"))
        self.assertFalse([v for v in NOT_SHOWN if v in buf.getvalue()])

    def test_a_symlinked_config_outside_git_is_not_followed(self) -> None:
        """git でないフォルダでも、 config/ そのもののリンクをたどって根の外を読まない。"""
        with tempfile.TemporaryDirectory() as td:
            outside, root = Path(td) / "outside", Path(td) / "repo"
            _write(outside, {k[len("config/"):]: v for k, v in a_files().items()
                             if k.startswith("config/")})
            _write(root, {k: v for k, v in a_files().items() if not k.startswith("config/")})
            os.symlink(outside, root / "config")
            self.assertIn("config", I.list_files(root))
            code, out = run_in(I, root)
        self.assertEqual(code, 2)
        self.assertIn("ERROR [config-missing]", out)

    def test_long_reference_lines_stay_fast(self) -> None:
        """deploy の長い行でも、 参照の探し方の時間が 2 乗で増えない。"""
        e = chr(0xE9)                                    # Unicode の字（英数字の外）
        for line in ("config/" * 50000, "config." * 50000,
                     ("config/" + e) * 50000, (e + "config.x") * 50000):
            started = time.monotonic()
            I._REF_PATH.findall(line)
            I._REF_NAME.findall(line)
            self.assertLess(time.monotonic() - started, 5.0)


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
     "                self.refs(rel, text)\n", "                pass\n"),
    ("宣言の増え方: 増えても言わない",
     "    if grew:\n", "    if False:\n"),
    ("宣言: 別名（*）を通す",
     "if self.check_event(yaml.AliasEvent):", "if False:"),
    ("宣言: 同じ鍵の重複を通す",
     "            if k.value in seen:\n", "            if False:\n"),
    ("S2: NAS・AI の有無を照らさない",
     "            elif enabled is not prof.caps[cap]:\n", "            elif False:\n"),
    ("S3: 面ごとの設定のファイルの名前を照らさない",
     'if len(parts) != 4 or not self._face_file_name(parts[3].rsplit(".", 1)[0]):',
     "if False:"),
    ("S4: deploy/ の中の値を参考でなく不合格にする",
     "    if not found:\n        print(\"結果: PASS",
     "    if not found and not notes:\n        print(\"結果: PASS"),
    ("宣言: 数の鍵を通す（誤りの文に値が出る）",
     'if not isinstance(k, yaml.ScalarNode) or k.tag != "tag:yaml.org,2002:str":',
     "if not isinstance(k, yaml.ScalarNode):"),
    ("設定: JSON の同じ鍵を通す",
     "                if k in seen:\n", "                if False:\n"),
    ("S1: config/pdm の . で始まる部品を通す",
     'if any(part.startswith(".") for part in parts):', "if False:"),
    ("S1: config/pdm の .json・.txt 以外を通す",
     "if not rel.endswith(PDM_SUFFIXES):", "if False:"),
    ("S1: config/pdm の知らない名前を通す",
     "            if parts[0] in PDM_FACE_DIRS:\n", "            if True:\n"),
    ("S1: 決まった置き場の外を通す",
     'return "S1-file"                                 # 決まった置き場の外', "return None"),
    ("S1: 撮像側の置き場の下のフォルダを通す",
     'if rel.startswith(IA_DIR) and "/" not in rel[len(IA_DIR):]:',
     "if rel.startswith(IA_DIR):"),
    ("宣言: defaults/ の下を追加のファイルとして許す",
     'if path.startswith(PDM_DIR + "defaults/"):', "if False:"),
    ("層 1: 製造番号を照らさない",
     '                self.member("equipment.cameras.serial", s, "L1-serial", rel, line, path)\n',
     "                pass\n"),
    ("S4: config で始まる名前の参照を見ない",
     "            for m in _REF_NAME.finditer(ln):\n", "            for m in []:\n"),
    ("S4: versions.yaml を読まない",
     'S4_FILES = ("versions.yaml",)', "S4_FILES = ()"),
    ("S4: 配置先の絶対の道を、 この Repository の参照とみなす",
     'rf"(?<![{_A}./-])(?:', 'rf"(?<![{_A}.-])(?:'),
    ("網: 宣言した網の外の IP を通す",
     "hit = [n for n in self.prof.networks if ipaddress.IPv4Address(ip) in n]",
     "hit = list(self.prof.networks)"),
    ("網: 宣言より広い網の値を通す",
     "hit = [n for n in self.prof.networks if net.subnet_of(n)]",
     "hit = list(self.prof.networks)"),
    ("網: 網の数の上限を外す",
     "if len(p.networks) >= MAX_NETWORKS:", "if False:"),
    ("出力: 止めたときのファイルの道を伏せない",
     'where = "" if e.rel is None else f"  file={masks.path(e.rel)}"',
     'where = "" if e.rel is None else f"  file={e.rel}"'),
    ("宣言の増え方: 減ったものを数えない",
     "added, removed = len(head[cat] - before), len(before - head[cat])",
     "added, removed = len(head[cat] - before), 0"),
    ("宣言の増え方: 能力が増えても言わない",
     "changed, grew = True, grew or head.caps[k] is True", "changed, grew = True, grew"),
    ("宣言の増え方: 検査の性質が変わっても言わない",
     "changed = grew = True", "changed = True"),
    ("層 1: shot_request の項目をトリガとして照らさない",
     "and _is_trigger_entry(_get(doc.data, path))):", "and False):"),
    ("層 1: __ の後ろが region でなくても、 前半だけを見る",
     'for t in ((token, head) if sep and rest.startswith("region") else (token,)):',
     "for t in (token, head):"),
    ("層 1: <名>__region の語を、 前半で見ない",
     'for t in ((token, head) if sep and rest.startswith("region") else (token,)):',
     "for t in (token,):"),
    ("層 1: plc.address の host・port の外の鍵を照らさない",
     'if kind == "value" and n == 3 and path[1] == "address" and path[2] in ("host", "port"):',
     'if kind == "value" and n == 3 and path[1] == "address":'),
    ("層 1: plc.address が host と port の形でなくても通す",
     "if isinstance(addr, dict) and addr:",
     'if isinstance(addr, dict) and ("host" in addr or "port" in addr):'),
    ("層 1: JSON の鍵の中の IP を照らさない",
     "        for m in _IPV4.finditer(value):\n",
     '        for m in (() if kind == "key" else _IPV4.finditer(value)):\n'),
    ("層 1: 小数で書いた製造番号を照らさない",
     "    if isinstance(v, (int, float)):\n        return str(v)",
     "    if isinstance(v, int):\n        return str(v)"),
    ("S2: nas.enabled を照らさなかったことを言わない",
     "self.skipped.append((cap, rel))", "pass"),
    ("S3: inspection を照らさない",
     'if has_ins and d["inspection"] != prof.inspection:', "if False:"),
    ("S3: flow を照らさない",
     'if has_flow and d["flow"] != prof.flow:', "if False:"),
    ("S3: flow と inspection の両方・どちらも無い設定を通す",
     "if main and has_flow == has_ins:", "if False:"),
    ("参考: 説明の .md の中の値を照らさない",
     '                self.text_lines(rel, _read(root, rel), "note-endpoint", "note-name", self.notes)\n',
     "                pass\n"),
    ("PDM の置き場の JSON を、 撮像側の鍵の全部で撮像側の設定とみなす",
     "keys = _IA_ONLY_KEYS if rel.startswith(PDM_DIR) else _IA_KEYS",
     "keys = _IA_KEYS"),
    ("PDM の置き場に置いた撮像側の構成を照らさない",
     "keys = _IA_ONLY_KEYS if rel.startswith(PDM_DIR) else _IA_KEYS",
     "keys = () if rel.startswith(PDM_DIR) else _IA_KEYS"),
    ("S4: ./ と ../ で始まる参照を数えない",
     "(?:\\.\\.?/){{0,16}}(config/", "(config/"),
    ("S2: 主の設定の plc.enabled・ai.enabled が真偽でなくても止めない",
     'if main and cap != "nas" and not isinstance(enabled, bool):', "if False:"),
    ("S4: deploy そのもののリンクを見ない",
     'rel in S4_FILES or rel == "deploy" or rel.startswith("deploy/")',
     'rel in S4_FILES or rel.startswith("deploy/")'),
    ("S1: align_reference.txt を根でなくても許す",
     "if len(parts) == 1 and parts[0] in PDM_FIXED:", "if parts[-1] in PDM_FIXED:"),
    ("S5: 記録の中の値を参考に落とす",
     '"S5-endpoint", "S5-name", self.findings', '"note-endpoint", "note-name", self.notes'),
    ("S5: 雛形の README の外の .md も参考にする",
     "            if rel in RECORD_NOTES:\n", '            if rel.endswith(".md"):\n'),
    ("S5: 鍵 version の外の値も版とみなす",
     'r"(?<![A-Za-z0-9_-])(version', 'r"(?<![A-Za-z0-9_-])((?:version|revision)'),
    ("S5: 版と同じ行の残りも外す",
     'r"[0-9][0-9A-Za-z.+!_-]*\\2")', 'r"[0-9][^\\n]*")'),
    ("S5: 中身のある .gitkeep を飛ばす",
     '(rel.endswith("/.gitkeep") and p.stat().st_size == 0)', 'rel.endswith("/.gitkeep")'),
    ("S5: 記録の dir そのもののリンクを見ない",
     ' and rel not in ("deployment", "validation"):', ":"),
    ("S5: 検証の記録の案件の記号を照らさない",
     "        self.record_codes()\n", ""),
    ("宣言: /24 の網も断る（境。 #115）",
     "if net.prefixlen < MIN_PREFIX:", "if net.prefixlen <= MIN_PREFIX:"),
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
    """**PR の中身を実行しない。** 外の命令は git の一覧と読み出しだけ（構文の木で見る）。"""

    ALLOWED_GIT = {"ls-files", "cat-file"}

    def test_only_reading_git_commands(self) -> None:
        tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
        runs, helper_calls = [], []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = (f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Name) else getattr(f, "id", ""))
            self.assertNotIn(name, {"eval", "exec", "compile", "__import__", "os.system",
                                    "os.popen", "subprocess.Popen", "subprocess.call",
                                    "subprocess.check_call", "subprocess.check_output",
                                    "importlib.import_module", "runpy.run_path"})
            if name == "subprocess.run":
                runs.append(node)
            if name == "_git":
                helper_calls.append(node)
        self.assertTrue(runs)
        for node in runs:
            cmd = node.args[0]
            self.assertIsInstance(cmd, ast.List)
            self.assertEqual(ast.literal_eval(cmd.elts[0]), "git")
            second = cmd.elts[1]
            if isinstance(second, ast.Starred):          # _git の中（呼ぶ側を下で見る）
                continue
            self.assertIn(ast.literal_eval(second), self.ALLOWED_GIT)
            self.assertFalse([k for k in node.keywords if k.arg == "shell"])
        self.assertTrue(helper_calls)
        for node in helper_calls:
            self.assertIn(ast.literal_eval(node.args[1]), self.ALLOWED_GIT)


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

    def test_it_runs_before_the_callers_hook(self) -> None:
        """呼び出し側の hook を流す段（guard の回帰テスト）より前に照らす。"""
        names = [s.get("run", "") for s in self.steps]
        mine = next(i for i, r in enumerate(names) if self.STEP_RUN in r)
        guard = next(i for i, r in enumerate(names) if "test_guard_main.sh" in r)
        self.assertLess(mine, guard)

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

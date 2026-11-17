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

#: 新しい形（#171）の値。 製品の名前・機体の記号・機体の道も、 出力に出さない。
A_PRODUCT = "example_product_" + "a"
B_PRODUCT = "example_product_" + "b"
A_MACHINE = "EX-" + "A1"
B_MACHINE = "EX-" + "B1"
SRV = "/srv/" + "example/"
#: 先頭が 0 の数の製造番号（YAML 1.1 では引用符が無いと 8 進として読まれる）。
OCTAL_SERIAL = "0" + "1234567"

#: 出力に出てはいけない値（どの場面でも見る）。
NOT_SHOWN = (A_PROFILE, A_FLOW, A_IP, A_SERIAL, A_TRIGGER, B_PROFILE, B_FLOW, *B_IPS,
             *B_SERIALS, B_PLC_HOST, *B_TRIGGERS, B_GROUP, *B_ADDRS, CREDENTIAL, TOKEN,
             NUMBER_KEY, HEX_KEY, HEX_VALUE, NUMBER_SERIAL, FLOAT_SERIAL, *S5_IPS, VERSION4,
             A_PRODUCT, B_PRODUCT, A_MACHINE, B_MACHINE, SRV + "work_root", OCTAL_SERIAL)


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


DEV_IA = "dev/config/image-acquisition/"


def a_dev_config(**changes) -> dict:
    """A の開発の機体の設定（保存済み画像のカメラ・AI を切る）。"""
    d = a_config()
    d["camera"] = {"camera1": {"camera_type": "area", "sdk": "file",
                               "image_dir": "../samples"}}
    d["ai"] = {"enabled": False}
    d.update(changes)
    return d


def d_files(**changes) -> dict:
    """前の形（`recipes/case.yaml` が無い）で、 据え付けの計画が無く、 開発の機体だけの設定だけを置く
    案件 A のリポジトリ（#182）。 `config/` には雛形の説明だけがある。 鍵は `_with` と同じ。"""
    return _with({
        "customer-profile.yaml": _yaml(a_profile()),
        "config/README.md": "# config/\n",
        "dev/README.md": "# dev\n",
        DEV_IA + "example.json": _json(a_dev_config()),
        "versions.yaml": "schema_version: 1\n",
    }, **changes)


# ---- 新しい形（recipes/case.yaml がある Repository。 #171）。 形は development-docs の
# `03_customer_development/recipes_and_machines.md`。 値はすべて架空で、 上の 2 つの案件のもの。

CASE = "recipes/case.yaml"
A_PRODUCT_FILE = "recipes/products/" + A_PRODUCT + ".yaml"
A_MACHINE_FILE = "machines/" + A_MACHINE + ".yaml"
EXAMPLE = "machines/dev.example.yaml"
NSERVICE = "deploy/app/example.service"


def n_profile(**kw) -> dict:
    """案件 A の宣言に、 製品の名前を足したもの。"""
    p = a_profile()
    p["products"] = [A_PRODUCT]
    p.update(kw)
    return p


def a_case(**profile_changes) -> dict:
    """案件 A の `recipes/case.yaml`。 撮像側の案件の設定から、 機体の値と `case` に書く名前を除いた物。"""
    prof = a_config()
    del prof["_profile"]
    prof["camera"] = {"camera1": {"camera_type": "area", "sdk": "example_sdk", "exposure_time": 1000}}
    prof.update(profile_changes)
    return {"schema_version": 1,
            "case": {"name": A_PROFILE, "title": "例の案件", "description": "例の説明"},
            "profile": prof}


def a_product(**changes) -> dict:
    """案件 A の `recipes/products/<製品>.yaml`（見本帳づくりが照合の型を選ぶ形）。"""
    d = {"schema_version": 1, "product": A_PRODUCT,
         "capture": {"crop": {"x": 0, "y": 0, "w": 100, "h": 100}, "exposure_time": 1000,
                     "gain": 0, "note": "例"},
         "extract": {"sam": {"model_type": "example", "checkpoint": "example.pth"}},
         "judge": {"opt_level": "auto"}}
    d.update(changes)
    return d


def a_machine(**changes) -> dict:
    """案件 A の据え付けた機体の設定（`machines/<記号>.yaml`）。"""
    d = {"schema_version": 1, "machine": {"symbol": A_MACHINE, "kind": "installed"},
         "work_root": SRV + "work_root", "weights_root": SRV + "weights",
         "ports": {"ui": 8000, "pdm": 8001}, "extra_origins": [],
         "profile": {"camera": {"camera1": {"serial_number": A_SERIAL, "ip_address": A_IP,
                                            "rotate": 0}},
                     "paths": {"data": SRV + "data", "dataset": SRV + "dataset"}}}
    d.update(changes)
    return d


def a_example() -> dict:
    """開発の機体の設定の見本（`machines/dev.example.yaml`）。 値を入れない。"""
    return {"schema_version": 1, "machine": {"symbol": "DEV", "kind": "dev"},
            "work_root": "/path/to/work_root", "weights_root": "/path/to/weights",
            "ports": {"ui": 8000, "pdm": 8001}, "extra_origins": [],
            "profile": {"camera": {"camera1": {"sdk": "file", "folder": "/path/to/samples",
                                               "loop": True, "serial_number": None,
                                               "ip_address": None}},
                        "paths": {"data": "/path/to/data", "dataset": "/path/to/dataset"}}}


def n_files(profile=None, case=None, product=None, machine=None, example=None) -> dict:
    """案件 A の新しい形の Repository（前の形の config/ は無い）。"""
    return {
        "customer-profile.yaml": _yaml(profile or n_profile()),
        "recipes/README.md": "# recipes/\n",
        CASE: _yaml(case or a_case()),
        A_PRODUCT_FILE: _yaml(product or a_product()),
        "recipes/capture/" + A_PRODUCT + ".json": _json({"crop": {"x": 0, "y": 0, "w": 100, "h": 100}}),
        "machines/README.md": "# machines/\n",
        "machines/.gitkeep": "",
        A_MACHINE_FILE: _yaml(machine or a_machine()),
        EXAMPLE: _yaml(example or a_example()),
        NSERVICE: f"ExecStart=/usr/bin/true --machine ./{A_MACHINE_FILE}\n",
        # 雛形の versions.yaml と同じく、 注釈で開発の固定に触れる（注釈は照らさない）
        "versions.yaml": "# 開発中の固定は、 このファイルでなく versions.dev.yaml に書く\nschema_version: 1\n",
    }


def b_case() -> dict:
    """案件 B の `recipes/case.yaml`（PLC の表はレシピ。 宛先は機体の設定）。"""
    c = b_config()
    del c["_profile"]
    c["camera"] = {f"camera{i + 1}": {"camera_type": "area"} for i in range(5)}
    del c["plc"]["address"]
    return {"schema_version": 1, "case": {"name": B_PROFILE}, "profile": c}


def b_machine() -> dict:
    return {"schema_version": 1, "machine": {"symbol": B_MACHINE, "kind": "shown"},
            "work_root": SRV + "work_root",
            "profile": {"camera": {f"camera{i + 1}": {"ip_address": B_IPS[i],
                                                       "serial_number": B_SERIALS[i]}
                                   for i in range(5)},
                        "plc": {"address": {"host": B_PLC_HOST, "port": B_PLC_PORT}}}}


def nb_files(case=None, machine=None) -> dict:
    """案件 B の新しい形の Repository。"""
    return {
        "customer-profile.yaml": _yaml(dict(b_profile(), products=[B_PRODUCT])),
        CASE: _yaml(case or b_case()),
        "recipes/products/" + B_PRODUCT + ".yaml": _yaml(a_product(product=B_PRODUCT)),
        "machines/" + B_MACHINE + ".yaml": _yaml(machine or b_machine()),
        "versions.yaml": "schema_version: 1\n",
    }


def _set(d: dict, path: tuple, value) -> dict:
    """入れ子の対応表の道に値を置く（途中が無ければ作る）。"""
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value
    return d


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
    add("13i. 基準の宣言が今の決まりで読めない（使えない文字）なら、 空の宣言と比べて増えたと言う（#54）", [
        a_with(**{"customer-profile.yaml": bel}), a_files()], 0,
        args=("--base-ref", "{base}"),
        texts=("今の決まりでは読めません", "PROFILE EXPANSION DETECTED"), absent=("比べていません",))
    initial = (HERE.parents[1] / "templates" / "customer-project" / "customer-profile.yaml").read_text(encoding="utf-8")
    add("13j. 新しい顧客 Repository の初期形の宣言に、 初めて値を書いたら増えたと言う（#54）", [
        a_with(**{"customer-profile.yaml": initial}), a_files()], 0,
        args=("--base-ref", "{base}"),
        texts=("今の決まりでは読めません", "PROFILE EXPANSION DETECTED"), absent=("比べていません",))
    add("13k. 基準の宣言が大きすぎても、 空の宣言と比べて増えたと言う（#54 の Reviewer B）", [
        a_with(**{"customer-profile.yaml": "# " + "x" * (256 * 1024) + "\n"}), a_files()], 0,
        args=("--base-ref", "{base}"),
        texts=("今の決まりでは読めません", "PROFILE EXPANSION DETECTED"), absent=("比べていません",))
    add("13l. 基準の宣言の道がフォルダでも、 空の宣言と比べて増えたと言う（#54 の Reviewer B）", [
        a_with(**{"customer-profile.yaml": None, "customer-profile.yaml__x.txt": "x\n"}), a_files()], 0,
        args=("--base-ref", "{base}"),
        texts=("今の決まりでは読めません", "PROFILE EXPANSION DETECTED"), absent=("比べていません",))

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

    # ---- S6: 開発の機体だけの設定（dev/config/。 #156）。 機体へ配らないが、 同じに照らす
    dev = "dev__config__image-acquisition__"

    def a_dev(**changes) -> str:
        return _json(a_dev_config(**changes))

    add("S6a. 開発の設定と説明だけなら通る（宣言で使うものを切ってよい）", a_with(**{
        "dev__README.md": "# dev\n", "dev__config__image-acquisition__.gitkeep": "",
        dev + "example.json": a_dev()}), 0, texts=("dev/config/）のファイル 1",))
    add("S6b. 開発の設定に B の構成をまるごと置く", a_with(**{
        dev + "example.json": _json(b_config())}), 1,
        {"S2-plc", "S2-nas", "S3-profile", "S3-flow", "S3-camera", "S3-face",
         "L1-endpoint", "L1-serial", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    add("S6c. 開発の設定の中の、 宣言に無いカメラの IP は不合格", a_with(**{
        dev + "example.json": a_dev(camera={"camera1": {"ip_address": B_IPS[0]}})}), 1,
        {"L1-endpoint"})
    add("S6d. 宣言で使わないものを開発の設定で使うのは不合格", a_with(**{
        dev + "example.json": a_dev(nas={"enabled": True})}), 1, {"S2-nas"})
    for tag, rel, body in (("1", "dev__config__pdm__example.json", _json({})),
                           ("2", dev + "sub__example.json", _json({})),
                           ("3", "dev__config__example.yaml", "x: 1\n"),
                           ("4", dev + ".example.json", _json({})),
                           ("5", dev + ".gitkeep", "x\n"),
                           ("6", dev + "example.txt", "x\n")):
        add(f"S6e{tag}. dev/config/ の決まりの外のファイルは不合格", a_with(**{rel: body}), 1,
            {"S6-file"})
    add("S6f. dev/config/ の説明の .md の中の値は参考だけ", a_with(**{
        "dev__config__README.md": f"例 {B_IPS[1]}\n"}), 0, texts=("参考",))
    add("S6g. dev/config/ の下のリンクは読まずに断る", a_with(**{
        dev + "example.json": Link("../../../config/image-acquisition/config.json")}), 1,
        {"S6-link"})
    add("S6h. dev そのもののリンクは読まずに断る", a_with(**{"dev": Link("config")}), 1,
        {"S6-link"})
    add("S6i. dev/config そのもののリンクも読まずに断る", a_with(**{
        "dev__README.md": "# dev\n", "dev__config": Link("../config")}), 1, {"S6-link"})
    add("S6j. dev/config/ の外の dev/ は照らさない", a_with(**{
        "dev__scripts__run.sh": f"{B_IPS[2]} {B_TRIGGERS[0]}\n"}), 0)
    add("S6k. 対応表でない開発の設定は照らせないので止める", a_with(**{
        dev + "example.json": "[]\n"}), 2, error="config-field")
    add("S6l. 開発の設定の名前の中の宣言に無い trig_ の語は不合格", a_with(**{
        dev + B_TRIGGERS[0] + ".json": a_dev()}), 1, {"L1-name"})
    add("S6m. 開発の設定の中の、 宣言に無い trig_ の語も補いで照らす", a_with(**{
        dev + "example.json": a_dev(note=f"from {B_TRIGGERS[1]}")}), 1, {"L1-name"})
    add("S6o. 開発の設定の enabled が真偽でなければ不合格（主の設定として読まれる）", a_with(**{
        dev + "example.json": a_dev(ai={"enabled": 1})}), 1, {"S6-enabled"})
    add("S6n. 決まりの外のファイルの中の値も照らす", a_with(**{
        "dev__config__example.yaml": f"camera: {B_IPS[3]}\n"}), 1, {"S6-file", "L1-endpoint"})

    # ---- 前の形で config/ に説明のほかが無く、 開発の機体だけの設定だけを置く案件（#182）。
    # dev/config/ の設定を主の設定の代わりに照らす（開発の機体でも主の設定として読まれる）
    def no(key: str) -> str:
        d = a_dev_config()
        del d[key]
        return _json(d)

    add("D1. 開発の設定だけで宣言と合えば通る（config/ は雛形の説明と空の .gitkeep だけ）", d_files(**{
        "config__image-acquisition__.gitkeep": ""}), 0,
        texts=("主の設定の代わりに照らしました", "dev/config/）のファイル 1"))
    add("D1b. config/ がまったく無くても、 開発の設定を照らす", d_files(**{"config__README.md": None}), 0,
        texts=("主の設定の代わりに照らしました",))
    add("D2. 開発の設定に B の構成をまるごと置く", d_files(**{dev + "example.json": _json(b_config())}), 1,
        {"S2-plc", "S2-nas", "S3-profile", "S3-flow", "S3-camera", "S3-face",
         "L1-endpoint", "L1-serial", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    add("D3. 開発の設定の中の、 宣言に無いカメラの IP は不合格", d_files(**{
        dev + "example.json": a_dev(camera={"camera1": {"ip_address": B_IPS[0]}})}), 1,
        {"L1-endpoint"})
    add("D4. 宣言で使わないものを開発の設定で使うのは不合格", d_files(**{
        dev + "example.json": a_dev(nas={"enabled": True})}), 1, {"S2-nas"})
    add("D5. 開発の設定が 2 つなら、 どちらも主の設定の代わりに照らす", d_files(**{
        dev + "example2.json": a_dev(shot={"camera1": {"camera_type": "area",
                                                        "triggers": [A_TRIGGER, B_TRIGGERS[2]]}})}),
        1, {"L1-trigger"})
    add("D5b. 開発の設定が 2 つとも宣言と合えば通る", d_files(**{dev + "example2.json": a_dev()}), 0,
        texts=("dev/config/）のファイル 2",))
    add("D6. 主の設定の代わりなので、 flow も inspection も無ければ止める", d_files(**{
        dev + "example.json": no("flow")}), 2, error="config-field")
    add("D6b. 主の設定の代わりなので、 flow と inspection の両方があれば止める", d_files(**{
        dev + "example.json": a_dev(inspection={"axis_example": "value_example"})}), 2,
        error="config-field")
    for tag, key in (("", "plc"), ("b", "nas"), ("c", "ai")):
        add(f"D7{tag}. 主の設定の代わりなので、 {key} の節が無ければ止める", d_files(**{
            dev + "example.json": no(key)}), 2, error="config-field")
    add("D8. 開発の設定の enabled が真偽でなければ不合格（今の S6 と同じ）", d_files(**{
        dev + "example.json": a_dev(ai={})}), 1, {"S6-enabled"})
    bdev = b_config()
    bdev["plc"]["enabled"] = False
    bdev["nas"] = {"enabled": False}
    add("D9. 宣言で使う PLC・NAS を開発の機体で切るのは許す（主の設定の代わりでも）", {
        "customer-profile.yaml": _yaml(b_profile()), DEV_IA + "example.json": _json(bdev)}, 0)
    # config/ に説明のほかがある（据え付けの設定を書きかけた・消し忘れた形）なら、 今どおり止める
    for tag, key, body in (("", "config__image-acquisition__recipe.json", _json({})),
                           ("b", "config__pdm__align_reference.txt", "EXAMPLE-LOT-0001\n"),
                           ("c", "config__pdm__capture__.gitkeep", "x\n"),
                           ("d", "config__image-acquisition__config.json",
                            Link("../../dev/config/image-acquisition/example.json")),
                           ("f", "config__README.md", Link("../dev/README.md"))):
        add(f"D10{tag}. config/ に説明のほかがあれば、 開発の設定で代えずに止める", d_files(**{key: body}), 2,
            error="config-missing")
    add("D10e. config そのもののリンクも止める", d_files(**{
        "config__README.md": None, "config": Link("dev/config")}), 2, error="config-missing")
    add("D10g. Git の外（.gitignore）に主の設定があれば止める", d_files(**{
        ".gitignore": "/config/image-acquisition/config.json\n",
        "config__image-acquisition__config.json": _json(a_config())}), 2, error="config-missing")
    # どちらも無い・代わりにできる開発の設定が無いなら、 今どおり止める
    add("D11. config/image-acquisition/config.json も開発の設定も無ければ止める", d_files(**{
        dev + "example.json": None}), 2, error="config-missing")
    for tag, key, body in (("b", dev + "sub__example.json", a_dev()),
                           ("c", dev + ".example.json", a_dev()),
                           ("d", dev + "example.txt", "x\n"),
                           ("e", "dev__config__example.json", a_dev()),
                           ("f", dev + "example.json", Link("../../README.md"))):
        add(f"D11{tag}. 開発の設定が決まりの外かリンクだけなら、 代わりにせずに止める", d_files(**{
            dev + "example.json": None, key: body}), 2, error="config-missing")
    add("D12. --report-only は 0 で終える（所見は出す）", d_files(**{
        dev + "example.json": a_dev(camera={"camera1": {"ip_address": B_IPS[0]}})}), 0,
        {"L1-endpoint"}, args=("--report-only",), texts=("REPORT ONLY",))
    for tag, args in (("", ()), ("b", ("--report-only",))):
        add(f"D13{tag}. 宣言が初期形のままなら、 開発の設定だけでも止める", d_files(**{
            "customer-profile.yaml": initial}), 2, error="profile-schema", args=args)
    add("D14. deploy/ が無い主の設定に触れるのは不合格", d_files(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]}), 1, {"S4-missing"})
    add("D15. config/ があれば、 開発の設定は今のまま（主の設定として扱わない）", a_with(**{
        dev + "example.json": no("flow")}), 0, absent=("主の設定の代わりに照らしました",))
    add("D15b. config/ があれば、 宣言で使う PLC を開発の機体で切るのは今のまま許す", _with(b_files(), **{
        dev + "example.json": _json(bdev)}), 0)
    # 根の config/・dev/ の大文字小文字の書き違い。 範囲の外に設定を置いたまま、 小文字の置き場で照らして
    # 合格にしない（Reviewer B の所見。 書き違いの置き場の値は出力に出さない）
    add("D16. 根の Config/ に B の構成を置き、 開発の設定だけで照らす形は止める", d_files(**{
        "Config__image-acquisition__config.json": _json(b_config())}), 2, error="config-missing")
    add("D16b. config/ があっても、 根の Dev/ の書き違いがあれば止める", a_with(**{
        "Dev__config__image-acquisition__example.json": _json(b_config())}), 2, error="config-missing")

    # ---- S4: 据え付けの設定が、 開発の機体だけの設定を指さない（#156）
    add("S4-dev a. deploy/ が MIRUME_DEV_CONFIG を渡すのは不合格", a_with(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]
        + "Environment=MIRUME_DEV_CONFIG=/srv/example/x.json\n"}), 1, {"S4-dev"})
    add("S4-dev b. deploy/ が dev/config/ の道を指すのは不合格（配置先の絶対の道も）", a_with(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]
        + "ExecStart=/usr/bin/true /srv/example/dev/config/image-acquisition/x.json\n"}), 1,
        {"S4-dev"})
    add("S4-dev c. versions.yaml が ./dev/config/ を指すのも不合格", a_with(**{
        "versions.yaml": "schema_version: 1\nnote: ./dev/config/x.json\n"}), 1, {"S4-dev"})
    add("S4-dev d. 機器の道や似た名前は開発の設定とみなさない", a_with(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]
        + "Environment=CAMERA=/dev/video0\nEnvironment=X_MIRUME_DEV_CONFIG_Y=1\n"
        "# my-dev/config/ devconfig/ https://example.dev/config/x /srv/dev/x\n"}), 0)
    for tag, line in (("e", "ExecStartPre=/bin/cp -r ./dev /srv/example\n"),
                      ("f", "ExecStartPre=/bin/cp -r /srv/example/dev/config /srv/x\n"),
                      ("g", "Environment=INSPECTION_CONFIG=dev/x.json\n"),
                      ("h", "ExecStartPre=/bin/cp ../dev/x.json /srv/x\n"),
                      ("j", "export MIRUME_DEV_CONFIG\n"),
                      ("k", "ExecStart=/usr/bin/docker run -e MIRUME_DEV_CONFIG example\n"),
                      ("l", 'ExecStart=/usr/bin/true --config "${MIRUME_DEV_CONFIG}"\n'),
                      ("m", "Environment=\"MIRUME_DEV_CONFIG=/srv/x.json\"\n"),
                      ("n", "PassEnvironment=EXAMPLE_X MIRUME_DEV_CONFIG\n"),
                      ("o", "  - MIRUME_DEV_CONFIG\n"),
                      ("p", 'os.environ["MIRUME_DEV_CONFIG"] = "x"\n'),
                      ("q", "# MIRUME_DEV_CONFIG はここで渡す\n"),
                      ("r", "del x; os.environ['MIRUME_DEV_CONFIG'] = '/x'\n"),
                      ("s", "echo UnsetEnvironment= ; export MIRUME_DEV_CONFIG=/x\n"),
                      ("t", "unset EXAMPLE_X && export MIRUME_DEV_CONFIG=/x\n"),
                      ("u", "Environment=X=-u MIRUME_DEV_CONFIG=/srv/a.json\n"),
                      ("v", "ExecStart=/usr/bin/example --unset MIRUME_DEV_CONFIG\n"),
                      ("w", "p = os.environ.pop('MIRUME_DEV_CONFIG')\n"),
                      ("x", "ExecStart=/usr/bin/env -u EXAMPLE_X MIRUME_DEV_CONFIG=/x /usr/bin/true\n"),
                      ("y", "Environment=X=unset MIRUME_DEV_CONFIG=/srv/a.json\n"),
                      ("z", "ExecStart=/usr/bin/env -u -u MIRUME_DEV_CONFIG=/srv/a.json /usr/bin/true\n"),
                      ("aa", "Environment=X=env -u MIRUME_DEV_CONFIG=/srv/a.json\n"),
                      ("ab", "Environment=X=UnsetEnvironment=MIRUME_DEV_CONFIG=/srv/a.json\n")):
        add(f"S4-dev {tag}. deploy/ が dev/ を写す・指す形も不合格", a_with(**{
            SERVICE.replace("/", "__"): a_files()[SERVICE] + line}), 1, {"S4-dev"})
    add("S4-dev i. 製品の起動が環境から外す形（env -u・unset）は守りなので断らない", a_with(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]
        + "ExecStart=/usr/bin/env -u MIRUME_DEV_CONFIG -u EXAMPLE_X /usr/bin/true\n"
        "ExecStartPre=/bin/sh -c 'unset -v EXAMPLE_X MIRUME_DEV_CONFIG'\n"
        "UnsetEnvironment=EXAMPLE_X MIRUME_DEV_CONFIG\n"
        "ExecStartPre=/usr/bin/env --unset=MIRUME_DEV_CONFIG /usr/bin/true\n"
        'os.environ.pop("MIRUME_DEV_CONFIG", None)\n'
        'del os.environ["MIRUME_DEV_CONFIG"]\n'
        'env = dict(os.environ); env.pop("MIRUME_DEV_CONFIG", None)\n'
        "del env['MIRUME_DEV_CONFIG']\n"}), 0)
    add("S4-dev i2. 行を \\ で続けた env の続きの行の -u も外す形（起動の script の形）", a_with(**{
        "deploy__app__run.sh": "#!/bin/sh\nexec env \\\n  -u EXAMPLE_X -u MIRUME_DEV_CONFIG \\\n"
        "  -u EXAMPLE_Y \\\n  /usr/bin/true\n"}), 0)
    add("S4-dev i6. 行の終わりの -u の名前を次の行に書いた形も外す形（行を \\ で続けた命令）", a_with(**{
        "deploy__app__run.sh": "#!/bin/sh\nexec env -u EXAMPLE_X -u \\\n  MIRUME_DEV_CONFIG \\\n  /usr/bin/true\n"}), 0)
    add("S4-dev i4. env に渡す -u だけを並べた行（配列）も外す形", a_with(**{
        "deploy__app__run.sh": "#!/bin/bash\nSTRIP=(\n  -u EXAMPLE_X -u MIRUME_DEV_CONFIG -u EXAMPLE_Y\n)\n"
        'exec env "${STRIP[@]}" /usr/bin/true\n'}), 0)
    add("S4-dev i5. -u の並びの後ろで渡す形は不合格", a_with(**{
        "deploy__app__run.sh": "#!/bin/bash\nSTRIP=(\n  -u EXAMPLE_X MIRUME_DEV_CONFIG=/srv/x.json\n)\n"}), 1,
        {"S4-dev"})
    add("S4-dev i3. 行を \\ で続けても、 渡す形は不合格", a_with(**{
        "deploy__app__run.sh": "#!/bin/sh\nexec env -u EXAMPLE_X \\\n  MIRUME_DEV_CONFIG=/srv/x.json \\\n"
        "  /usr/bin/true\n"}), 1, {"S4-dev"})

    # ---- 新しい形（recipes/case.yaml がある。 #171）。 前の形の照らしは変えない
    def n_with(**changes) -> dict:
        return _with(n_files(), **{k.replace("/", "__"): v for k, v in changes.items()})

    old_config = {k: v for k, v in a_files().items() if k.startswith("config/")}
    add("N1. 新しい形の正しい設定（主の設定 config.json は無くてよい）", n_files(), 0,
        texts=("新しい形（recipes/case.yaml がある）", "（recipes/・machines/）のファイル 5"))
    add("N1b. 移る間は前の形の config/ も残せる（両方を照らす）", _with(n_files(), **{
        k.replace("/", "__"): v for k, v in old_config.items()}), 0)
    mixed = {k: v for k, v in a_files(config=dict(a_config(), plc=b_plc(False))).items()
             if k.startswith("config/")}
    add("N1c. 両方あるときは、 前の形の config/ の混入も見つける", _with(n_files(), **{
        k.replace("/", "__"): v for k, v in mixed.items()}), 1,
        {"S2-plc", "L1-plc-endpoint", "L1-plc-address", "L1-trigger"})
    add("N1d. 前の形（case.yaml が無い）の、 今の雛形の recipes/ と説明だけの machines/ は今のまま照らさない",
        a_with(**{
            "recipes__README.md": "# recipes/\n", "recipes__x.yaml": f"peer: '{B_IPS[0]}'\n",
            "recipes__capture__README.md": f"{B_IPS[0]}\n",
            "recipes__capture__" + A_PRODUCT + ".json": _json({"host": B_IPS[0]}),
            "recipes__products__README.md": "# products/\n", "machines__README.md": "# machines/\n",
            "machines__.gitkeep": "",
            SERVICE.replace("/", "__"): a_files()[SERVICE] + "# versions.dev.yaml\n"}), 0)
    # case.yaml が無いのに、 新しい形にしか無いファイルがある（置き忘れ・書き違い）。 照らさずに通さない
    for tag, rel in (("e", "machines__" + B_MACHINE + ".yaml"),
                     ("f", "recipes__products__" + B_PRODUCT + ".yaml"),
                     ("g", "recipes__case.yml"),
                     ("h", "recipes__Case.yaml"),
                     ("i", "machines__.gitkeep")):
        add(f"N1{tag}. case.yaml が無いのに新しい置き場がある・書き違いなら止める", a_with(**{
            rel: "x: 1\n"}), 2, error="config-missing")

    # 層 1: 決まった場所（機体の設定とレシピの profile）
    m = a_machine()
    m["profile"]["camera"]["camera1"]["ip_address"] = B_IPS[0]
    add("N2. 機体の設定のカメラの IP が宣言に無い", n_files(machine=m), 1, {"L1-endpoint"})
    m = a_machine()
    m["profile"]["camera"]["camera1"]["serial_number"] = B_SERIALS[0]
    add("N3. 機体の設定の製造番号が宣言に無い", n_files(machine=m), 1, {"L1-serial"})
    p = n_profile()
    p["equipment"]["cameras"][0]["serial"] = OCTAL_SERIAL
    m = _yaml(a_machine()).replace(f"serial_number: {A_SERIAL}", f"serial_number: {OCTAL_SERIAL}")
    add("N3b. 引用符の無い、 先頭が 0 の数の製造番号は、 書いたままの字で照らす",
        n_with(**{"customer-profile.yaml": _yaml(p), A_MACHINE_FILE: m}), 0)
    m = a_machine()
    m["profile"]["camera"]["camera2"] = {"rotate": 90}
    add("N3c. 機体の設定のカメラの役が宣言に無い", n_files(machine=m), 1, {"S3-camera"})
    m = a_machine()
    m["profile"]["plc"] = {"address": {"host": B_PLC_HOST, "port": B_PLC_PORT}}
    add("N4. PLC を使わない案件の機体の設定に PLC の宛先", n_files(machine=m), 1, {"L1-plc-endpoint"})
    m = a_machine(extra_origins=["http://" + B_IPS[1] + ":8000"])
    add("N5. 補い: profile の外（extra_origins）の、 宣言に無い IP", n_files(machine=m), 1,
        {"L1-endpoint"})
    add("N5b. 補い: 宣言した宛先なら、 profile の外でも通す",
        n_files(profile=n_profile(endpoints=[B_IPS[1]]), machine=m), 0)
    pr = a_product()
    pr["extract"]["note"] = B_TRIGGERS[0]
    add("N6. 補い: 製品のレシピの中の、 宣言に無い trig_ の語", n_files(product=pr), 1, {"L1-name"})
    add("N7. 補い: 機体の設定のファイルの名前の、 宣言に無い trig_ の語", n_with(**{
        "machines/" + B_TRIGGERS[1] + ".yaml": _yaml(a_machine())}), 1, {"L1-name"})
    add("N8. 補い: 移る間の撮像の条件（JSON）の中の、 宣言に無い IP", n_with(**{
        "recipes/capture/" + A_PRODUCT + ".json": _json({"host": B_IPS[2]})}), 1, {"L1-endpoint"})
    add("NB1. B の新しい形（PLC の表はレシピ、 宛先は機体の設定）", nb_files(), 0)
    m = b_machine()
    m["profile"]["plc"]["address"]["host"] = _ip(2, 99)
    add("NB2. 機体の設定の PLC の宛先が宣言に無い", nb_files(machine=m), 1, {"L1-plc-endpoint"})
    c = b_case()
    c["profile"]["plc"]["to_plc_example"]["example_signal"]["data"] = "EXAMPLE_ADDR_" + "999"
    add("NB3. レシピの PLC の番地が宣言に無い", nb_files(case=c), 1, {"L1-plc-address"})
    c = b_case()
    c["profile"]["shot"]["camera1"]["triggers"].append(A_TRIGGER)
    add("NB4. レシピの shot のトリガが宣言に無い", nb_files(case=c), 1, {"L1-trigger"})
    c = b_case()
    c["profile"]["plc"]["address"] = {"host": B_PLC_HOST, "port": B_PLC_PORT}
    add("NB5. レシピに PLC の宛先（機体だけの鍵）", nb_files(case=c), 1, {"R-key"})

    # S1: 置いてよいファイル
    for tag, rel, body, codes in (
            ("1", "recipes/x.yaml", "x: 1\n", ()),
            ("2", "recipes/products/sub/x.yaml", "x: 1\n", ()),
            ("3", "machines/sub/x.yaml", "x: 1\n", ()),
            ("4", "machines/x.txt", f"{B_IPS[3]}\n", ("L1-endpoint",)),
            ("5", "recipes/capture/x.csv", "x\n", ()),
            ("6", "machines/.hidden.yaml", _yaml(a_machine()), ()),
            ("7", "recipes/case.yml", "x: 1\n", ()),
            ("8", "recipes/.gitkeep", f"{B_IPS[3]}\n", ("L1-endpoint",)),
            ("9", "recipes/products/" + A_PRODUCT + ".yml", "x: 1\n", ())):
        add(f"N9-{tag}. 新しい置き場の決まりの外のファイル", n_with(**{rel: body}), 1,
            {"S1-new-file", *codes})
    add("N10. 新しい置き場の下のリンクは読まずに断る", n_with(**{
        "recipes/products/link.md": Link("../case.yaml")}), 1, {"S1-new-link"})
    no_machines = {k: None for k in n_files() if k.startswith("machines/")}
    add("N10b. machines そのもののリンクも読まずに断る（指していた設定は Git の外）", _with(n_with(**no_machines), **{
        "machines": Link("recipes")}), 1, {"S1-new-link", "S4-dev"})
    add("N10c. recipes そのもののリンクは、 case.yaml を読めないので止める", _with(n_files(), **{
        k.replace("/", "__"): None for k in n_files() if k.startswith("recipes/")}, **{
        "recipes_real__case.yaml": _yaml(a_case()), "recipes": Link("recipes_real")}), 2,
        error="config-missing")
    add("N10d. case.yaml がリンクなら止める", n_with(**{
        "recipes/real.md": _yaml(a_case()), CASE: Link("real.md")}), 2, error="config-missing")
    extra = {"config_files": {"extra": [{"path": "recipes/extra.yaml", "reason": "架空の追加"}]}}
    add("N11. 宣言した追加のファイル（recipes/ の下）", n_with(**{
        "customer-profile.yaml": _yaml(n_profile(**extra)), "recipes/extra.yaml": "memo: x\n"}), 0)
    add("N11b. 宣言の追加のファイルは config/・recipes/・machines/ の下だけ", n_files(profile=n_profile(
        config_files={"extra": [{"path": "deploy/x.yaml", "reason": "架空"}]})), 2,
        error="profile-schema")
    add("N12. 新しい置き場の説明の .md の中の値は参考だけ", n_with(**{
        "machines/README.md": f"例 {B_IPS[0]}\n"}), 0, texts=("参考（強制しません",))

    # S2・S3: recipes/case.yaml の profile と case.name
    add("N13. PLC を使わない案件のレシピに B の PLC の表", n_files(case=a_case(plc=b_plc(False))), 1,
        {"S2-plc", "L1-plc-endpoint", "L1-plc-address", "L1-trigger", "R-key"})
    add("N13b. NAS の有無がレシピと宣言で違う", n_files(case=a_case(nas={"enabled": True})), 1,
        {"S2-nas"})
    add("N13c. AI の有無がレシピと宣言で違う", n_files(case=a_case(ai={"enabled": False})), 1,
        {"S2-ai"})
    c = a_case()
    del c["profile"]["plc"]
    add("N13d. レシピに plc の節が無ければ止める（推測しない）", n_files(case=c), 2,
        error="config-field")
    c = a_case()
    c["case"]["name"] = B_PROFILE
    add("N14. case.name が宣言に無い", n_files(case=c), 1, {"S3-profile"})
    add("N14b. レシピの flow が宣言と違う", n_files(case=a_case(flow=B_FLOW)), 1, {"S3-flow"})
    c = a_case()
    c["profile"]["camera"]["camera2"] = {"camera_type": "area"}
    add("N14c. レシピのカメラの役が宣言に無い", n_files(case=c), 1, {"S3-camera"})
    c = a_case()
    c["profile"]["face_dict"][B_TRIGGERS[3]] = "面"
    add("N14d. レシピの面が宣言に無い", n_files(case=c), 1, {"S3-face"})
    c = a_case()
    del c["case"]["name"]
    add("N14e. case.name が無ければ止める", n_files(case=c), 2, error="config-field")
    add("N14f. profile に書いた _profile も照らす", n_files(case=a_case(_profile=B_PROFILE)), 1,
        {"S3-profile"})
    add("N14g. 全体が対応表でないレシピは止める", n_with(**{CASE: "- 1\n"}), 2, error="config-field")

    # 製品の名前
    # ファイルの名前だけが宣言に無い形（product の値は宣言の中）で、 ファイルの名前の照らしを確かめる
    add("N15. 宣言に無い製品の名前のファイル", n_with(**{
        "recipes/products/" + B_PRODUCT + ".yaml": _yaml(a_product())}), 1, {"S3-product"})
    add("N15b. product の値が宣言に無い", n_files(product=a_product(product=B_PRODUCT)), 1,
        {"S3-product"})
    extra = {"config_files": {"extra": [{"path": "recipes/products/" + B_PRODUCT + ".yaml",
                                         "reason": "架空"}]}}
    add("N15c. 宣言に無い製品のファイルは、 追加のファイルとしても許さない", n_with(**{
        "customer-profile.yaml": _yaml(n_profile(**extra)),
        "recipes/products/" + B_PRODUCT + ".yaml": _yaml(a_product())}), 1, {"S3-product"})

    # 新しく足す照らし: レシピに書かない鍵
    c = a_case()
    for path, value in ((("camera", "camera1", "serial_number"), A_SERIAL),
                        (("camera", "camera1", "ip_address"), A_IP),
                        (("camera", "camera1", "device_index"), 0),
                        (("camera", "camera1", "folder"), SRV + "samples"),
                        (("camera", "camera1", "loop"), True),
                        (("nas", "mount_path"), SRV + "nas"),
                        (("paths", "data"), SRV + "data"),
                        (("paths", "dataset"), SRV + "dataset"),
                        (("paths", "work"), SRV + "work"),
                        (("pdm_dir",), SRV + "work"),
                        (("pdm_dir_master",), SRV + "work")):
        _set(c["profile"], path, value)
    # paths は、 下の鍵がすべて書かない鍵なので paths そのものを 1 つ断る（下の 3 つは数えない）
    add("N16. レシピの profile の、 機体だけ・開発の機体だけ・組み立て器が組む鍵", n_files(case=c), 1,
        {"R-key"}, texts=("内訳: R-key 9",))
    add("N16d. paths を字で書いても断る（値の型によらず）", n_files(case=a_case(paths=SRV + "data")), 1,
        {"R-key"})
    c = b_case()
    c["profile"]["plc"]["address"] = f"{B_PLC_HOST}:{B_PLC_PORT}"
    add("N16e. plc.address を字で書いても断る（値の型によらず）", nb_files(case=c), 1, {"R-key"})
    c = a_case()
    c["profile"]["camera"]["camera1"] = "serial " + A_SERIAL
    add("N16f. カメラの役を対応表で書かなければ、 照らせないので止める", n_files(case=c), 2,
        error="config-field", texts=("profile.camera.<*> を対応表で",))
    add("N16g. nas を対応表で書かなければ、 照らせないので止める", n_files(case=a_case(nas=SRV + "nas")), 2,
        error="config-field")
    c = a_case()
    c["profile"]["camera"]["camera1"]["sdk"] = "file"
    add("N16b. 保存済みの画像のカメラ（sdk: file）はレシピに書かない", n_files(case=c), 1, {"R-key"})
    c = a_case(nas={"enabled": False, "keep_back_days": 30}, ai_workers=1)
    c["profile"]["camera"]["camera1"].update(rotate=0, gain=0, apply_settings=True)
    add("N16c. 機体で上書きできる鍵の既定は、 レシピに書ける", n_files(case=c), 0)
    pr = a_product()
    for path in (("name",), ("save_name",), ("capture", "verified"), ("extract_mark",),
                 ("updated_at",), ("extract_history",), ("extract_history_broken",),
                 ("extract", "_meta"), ("judge", "_meta"), ("extract", "sam", "device"),
                 ("judge", "device"), ("judge", "threshold"), ("judge", "threshold_bank_at"),
                 ("judge", "dino_scale"), ("judge", "print_fields_geom"),
                 ("judge", "print_fields_sig")):
        _set(pr, path, 1)
    add("N17. 製品のレシピに書かない鍵", n_files(product=pr), 1, {"R-key"},
        texts=("内訳: R-key 16",))
    pr = a_product()
    for k in ("match", "window_radius", "edge_margin", "method", "layers", "top_percent"):
        pr["judge"][k] = 1
    add("N18. opt_level が auto なのに、 見本帳づくりが決める照合の型がある", n_files(product=pr), 1,
        {"R-opt"}, texts=("内訳: R-opt 6",))
    pr["judge"]["opt_level"] = "strict"
    add("N18b. opt_level が auto でなければ、 照合の型を人が決めた値として書ける", n_files(product=pr), 0)

    # 新しく足す照らし: 機体の設定（Git にある kind: dev・見本の値）
    for tag, kind, code in (("", "dev", None), ("b", "example-kind", "config-field"),
                            ("c", None, "config-field"), ("d", "shown", None)):
        m = a_machine()
        if kind is None:
            del m["machine"]["kind"]
        else:
            m["machine"]["kind"] = kind
        files = n_with(**{"machines/EX-" + "C1.yaml": _yaml(m)})
        if code:
            add(f"N19{tag}. machine.kind が installed・shown・dev のどれでもなければ止める", files, 2,
                error=code)
        elif kind == "dev":
            add("N19. Git にある開発の機体の設定（kind: dev）", files, 1, {"M-dev"})
        else:
            add("N19d. 顧客に見せる機体（shown）の設定は置ける", files, 0)
    for tag, change, codes in (
            ("", (("profile", "camera", "camera1", "ip_address"), A_IP), {"M-example-endpoint"}),
            ("b", (("extra_origins",), ["http://" + "127.0.0.1" + ":8000"]), {"M-example-endpoint"}),
            ("c", (("profile", "camera", "camera1", "serial_number"), A_SERIAL), {"M-example-serial"}),
            ("d", (("work_root",), SRV + "work_root"), {"M-example-path"}),
            ("e", (("weights_root",), "/path/to/../../srv/example/weights"), {"M-example-path"}),
            ("f", (("profile", "paths", "data"), "C:\\example\\data"), {"M-example-path"})):
        e = a_example()
        _set(e, *change)
        add(f"N20{tag}. 見本に値（IP・製造番号・/path/to/ の外の絶対の道）を入れない",
            n_files(example=e), 1, codes)
    e = a_example()
    e["machine"]["kind"] = "installed"
    add("N20g. 見本の machine.kind が dev でない", n_files(example=e), 1, {"M-example-kind"})
    e = a_example()
    del e["machine"]
    add("N20h. 見本に machine が無い", n_files(example=e), 1, {"M-example-kind"})

    # 新しく足す照らし: 機体の設定の profile に書けるのは、 機体だけの鍵・機体で上書きできる鍵（と開発の機体だけの鍵）
    m = a_machine()
    m["profile"].update(_profile=A_PROFILE, flow=A_FLOW, pdm_dir=SRV + "work", plc={"enabled": False})
    m["profile"]["paths"]["work"] = SRV + "work"
    m["profile"]["camera"]["camera1"].update(sdk="example_sdk", folder=SRV + "samples")
    add("N24. 機体の設定の profile に、 機体に書けない鍵", n_files(machine=m), 1, {"M-key"},
        texts=("内訳: M-key 7",))
    m = a_machine()
    m["profile"]["camera"]["camera1"].update(device_index=0, exposure_time=1000, gain=0,
                                             apply_settings=True)
    m["profile"].update(nas={"mount_path": SRV + "nas", "keep_back_days": 30}, ai_workers=1)
    add("N24b. 機体の設定には、 機体だけの鍵と機体で上書きできる鍵を書ける", n_files(machine=m), 0)
    m = a_machine()
    m["profile"]["camera"]["camera1"].update(sdk="file", loop=True)
    add("N24c. 据え付けた機体の設定には、 開発の機体だけが書ける鍵を書かない", n_files(machine=m), 1,
        {"M-key"}, texts=("内訳: M-key 2",))
    e = a_example()
    e["profile"]["camera"]["camera1"]["sdk"] = "example_sdk"
    add("N24d. 見本でも、 sdk は file のときだけ機体に書ける", n_files(example=e), 1, {"M-key"})
    m = b_machine()
    m["profile"]["plc"]["address"] = f"{B_PLC_HOST}:{B_PLC_PORT}"
    add("N24e. 機体の設定の plc.address を対応表で書かなければ、 照らせないので止める", nb_files(machine=m), 2,
        error="config-field")

    # S4: 新しい形では、 開発の固定・見本・Git の外の機体の設定も指さない
    for tag, line in (("", f"ExecStartPre=/bin/cp {SRV}versions.dev.yaml /tmp/x\n"),
                      ("b", f"ExecStart=/usr/bin/true --machine {SRV}{EXAMPLE}\n"),
                      ("c", "ExecStart=/usr/bin/true --machine ./machines/EX-" + "D9.yaml\n"),
                      ("d", "Environment=MIRUME_DEV_CONFIG=/srv/x.yaml\n"),
                      ("g", "ExecStart=/usr/bin/true --machine %h/example/machines/EX-" + "D9.yaml\n"),
                      ("h", "ExecStart=/usr/bin/true --machine ${REPO}/machines/EX-" + "D9.yaml\n")):
        add(f"N21{tag}. 新しい形の deploy/ が開発の機体だけの物を指す", n_with(**{
            NSERVICE: n_files()[NSERVICE] + line}), 1, {"S4-dev"})
    add("N21e. versions.yaml の値が versions.dev.yaml を指す", n_with(**{
        "versions.yaml": "schema_version: 1\nref: versions.dev.yaml\n"}), 1, {"S4-dev"})
    add("N21i. 前に道があっても、 Git にある機体の設定を指すのは通す", n_with(**{
        NSERVICE: f"ExecStart=/usr/bin/true --machine %h/example/{A_MACHINE_FILE}\n"}), 0)
    add("N21f. 前の形の S4 は今のまま（versions.dev.yaml を見ない）", a_with(**{
        SERVICE.replace("/", "__"): a_files()[SERVICE]
        + f"ExecStartPre=/bin/cp {SRV}versions.dev.yaml /tmp/x\n"}), 0)

    # レシピと機体の設定の YAML の形
    for tag, rel, text, want in (
            ("", CASE, "case: [1\n", None),
            ("b", A_PRODUCT_FILE, _yaml(a_product()) + f"product: {B_PRODUCT}\n", "同じ鍵が 2 回"),
            ("c", A_MACHINE_FILE, _yaml(a_machine()) + "x: &a 1\ny: *a\n", "別名"),
            ("d", A_PRODUCT_FILE, _yaml(a_product()) + f"{NUMBER_KEY}: x\n", "鍵は文字に"),
            ("e", A_MACHINE_FILE, _yaml(a_machine()) + "other:\n  <<: {k: 1}\n", "`<<` は使えません"),
            ("f", A_PRODUCT_FILE, _yaml(a_product()) + "tags: !!set {a: null}\n", "使えない型"),
            ("g", A_PRODUCT_FILE, _yaml(a_product()) + "blob: !!binary aGk=\n", "使えない型")):
        add(f"N22{tag}. 読めない・形の違うレシピと機体の設定は止める", n_with(**{rel: text}), 2,
            error="config-yaml", texts=(want,) if want else ())

    # 宣言の製品の名前
    add("N23. 製品の名前の宣言の増え方は、 種類と数だけを出す", [
        n_files(), n_files(profile=n_profile(products=[A_PRODUCT, B_PRODUCT]))], 0,
        args=("--base-ref", "{base}"),
        texts=("products: +1 / -0", "PROFILE EXPANSION DETECTED", "宣言して使っていないもの"))
    add("N23b. 製品の名前を数で書いたら、 引用符で囲むよう言う", n_files(profile=n_profile(
        products=[int(NUMBER_SERIAL)])), 2, error="profile-schema", texts=("引用符",))
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

    def test_pathspec_env_does_not_hide_config_or_strays(self) -> None:
        """環境に `GIT_LITERAL_PATHSPECS=1` などがあっても、 config/・dev/ と書き違いの置き場を一覧に引く
        （#182 の Reviewer B の所見。 外さないと `:(icase)` が効かず、 config/ が一覧から消えて黙って通る）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            _build(root, [_with(a_files(), **{"Config__image-acquisition__config.json": _json(b_config())})])
            for name in I._PATHSPEC_ENV:
                with self.subTest(env=name), mock.patch.dict(os.environ, {name: "1"}):
                    files, strays = I.list_files_and_strays(root)
                    self.assertIn("config/image-acquisition/config.json", files)
                    self.assertEqual(strays, ["Config"])

    def test_case_strays_outside_git(self) -> None:
        """Git でない根でも、 config/・dev/ の書き違いの置き場で止める（#182 の Reviewer A の所見）。"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "repo"
            _write(root, {k: v for k, v in d_files().items() if not k.startswith("config/")})
            (root / "Dev").mkdir()
            self.assertEqual(I.list_files_and_strays(root)[1], ["Dev"])
            code, out = run_in(I, root)
        self.assertEqual(code, 2, out)
        self.assertIn("ERROR [config-missing]", out)

    def test_a_symlinked_dev_config_is_not_used_for_the_main_config(self) -> None:
        """Git の一覧にある開発の設定でも、 置き場の途中がリンクなら、 主の設定の代わりにしない（#182）。
        リンクの先（根の外）の設定を照らして通さず、 照らす相手が無いので止める。"""
        with tempfile.TemporaryDirectory() as td:
            outside, root = Path(td) / "outside", Path(td) / "repo"
            _write(outside, {"image-acquisition/example.json": _json(b_config())})
            _build(root, [d_files()])
            shutil.rmtree(root / "dev" / "config")         # commit した後に、 置き場をリンクに替える
            os.symlink(outside, root / "dev" / "config")
            self.assertIn(DEV_IA + "example.json", I.list_files(root))
            code, out = run_in(I, root)
        self.assertEqual(code, 2, out)
        self.assertIn("ERROR [config-missing]", out)
        self.assertFalse([v for v in NOT_SHOWN if v in out])

    def test_long_unset_lines_stay_fast(self) -> None:
        """外す形の見分けが、 長い行で時間を 2 乗にしない（#156 の A の MINOR-5）。"""
        line = "del " * 20000 + "x; " + "UnsetEnvironment= " * 5000 + "MIRUME_DEV_CONFIG=/x"
        t0 = time.monotonic()
        self.assertTrue(I._dev_ref(line))
        self.assertLess(time.monotonic() - t0, 2.0)

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
    ("宣言の増え方: 基準の宣言が今の決まりで読めないと比べない（#54 の前）",
     "                    unreadable = True   # 空の宣言と比べる", "                    return []   # 空の宣言と比べる"),
    ("宣言の増え方: 大きすぎる・ファイルでない基準の宣言と比べない",
     "                unreadable = True   # 大きすぎる", "                return []   # 大きすぎる"),
    ("宣言: 別名（*）を通す",
     "if self.check_event(yaml.AliasEvent):", "if False:"),
    ("宣言: 同じ鍵の重複を通す",
     "            if k.value in seen:\n", "            if False:\n"),
    ("S2: NAS・AI の有無を照らさない",
     "            elif enabled is not prof.caps[cap] and not (dev and enabled is False):\n",
     "            elif False:\n"),
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
     'rf"(?<![{_A}./-])(?:\\.\\.?/){{0,16}}(config/', 'rf"(?<![{_A}.-])(?:\\.\\.?/){{0,16}}(config/'),
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
     "                # **参考だけ**: 説明の中の値（別の Repository から写すと、 説明にも値が入る）\n"
     '                self.text_lines(rel, _read(root, rel), "note-endpoint", "note-name", self.notes)\n',
     "                pass\n"),
    ("S6: 説明の .md の中の値を照らさない",
     '            if name.endswith(".md"):\n'
     '                self.text_lines(rel, _read(root, rel), "note-endpoint", "note-name", self.notes)\n',
     '            if name.endswith(".md"):\n'),
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
    ("S6: dev/config/ を照らさない（#156）",
     '            if rel not in ("dev", DEV_DIR[:-1]) and not rel.startswith(DEV_DIR):\n',
     "            if True:\n"),
    ("S6: dev/config そのもののリンクを見ない",
     'if rel not in ("dev", DEV_DIR[:-1]) and', 'if rel not in ("dev",) and'),
    ("S6: ファイルを集めるときに dev/ を見ない",
     '"validation", ":(icase)dev",', '"validation",'),
    ("書き違い: 根の config/・dev/ の大文字小文字の書き違いを見ない",
     "            if _is_case_stray(top):\n", "            if False:\n"),
    ("S6: 決まりの外のファイルを通す",
     "            code = self.s6(rel)\n", "            code = None\n"),
    ("S6: 撮像側の置き場の下のフォルダを通す",
     'if "/" not in rest and name.endswith(".json")', 'if name.endswith(".json")'),
    ("S6: . で始まる名前を通す",
     'name.endswith(".json") and not name.startswith(".")', 'name.endswith(".json")'),
    ("S6: 中身のある .gitkeep を通す",
     'return None if (self.root / rel).stat().st_size == 0 else "S6-file"', "return None"),
    ("S6: 開発の設定を撮像側の設定とみなさない",
     "if rel == IA_MAIN or rel.startswith(DEV_IA_DIR):", "if rel == IA_MAIN:"),
    ("S6: 開発の設定で、 宣言で使わないものを使っても通す",
     "and not (dev and enabled is False):", "and not dev:"),
    ("S6: 開発の設定で、 宣言で使うものを切るのを断る",
     "and not (dev and enabled is False):", ":"),
    ("S6: 開発の設定の名前の trig_ の語を照らさない",
     '            for part in rel.split("/")[2:]:\n', "            for part in ():\n"),
    ("S6: 決まりの外のファイルの中の値を照らさない",
     '            else:\n                self.text_lines(rel, _read(root, rel), "L1-endpoint"',
     '            elif False:\n                self.text_lines(rel, _read(root, rel), "L1-endpoint"'),
    ("S4: 開発の機体だけの設定を指すのを見ない（#156）",
     "            if _dev_ref(cmd + ln):\n", "            if False:\n"),
    ("S4: 前にほかの道がある dev/config を見ない",
     'rf"|(?<![{_A}.-])dev/config(?![{_A}-])', 'rf"|(?<![{_A}./-])dev/config(?![{_A}-])'),
    ("S4: 末尾の / の無い dev/config を見ない",
     "dev/config(?![{_A}-])", "dev/config/(?![{_A}-])"),
    ("S4: この Repository の dev/ の道を見ない",
     r"(?:\.\.?/){{0,16}}dev/|", r"(?:\.\.?/){{0,16}}dev/NEVER|"),
    ("S4: ./dev そのものを見ない",
     r'\.\.?/dev(?![{_A}./-])"', r'\.\.?/devNEVER(?![{_A}./-])"'),
    ("S6: 開発の設定の enabled が真偽でなくても通す",
     "            if dev and not isinstance(enabled, bool):\n", "            if False:\n"),
    ("S4: MIRUME_DEV_CONFIG の名前を見ない",
     "    return any(not _dev_unset(line[max(0, m.start() - _DEV_UNSET_SPAN):m.start()])\n",
     "    return False and any(not _dev_unset(line[max(0, m.start() - _DEV_UNSET_SPAN):m.start()])\n"),
    ("S4: 環境から外す形も断る",
     "    return any(rx.search(seg) for rx in _DEV_UNSET)\n", "    return False\n"),
    ("S4: env -u を環境から外す形とみなさない",
     '(?:-u[ \\t]+|--unset[= \\t])', '(?:-uNEVER[ \\t]+|--unset[= \\t])'),
    ("S4: env の無い -u も外す形とみなす",
     'rf"(?<![{_A}.=-])env(?:[ \\t][^;&|]*)?[ \\t](?<!-u[ \\t])(?<!--unset[ \\t])"\n               rf"(?:-u',
     'rf"(?:-u'),
    ("S4: = の値の中の env も外す形とみなす",
     "(?<![{_A}.=-])env(", "(?<![{_A}.-])env("),
    ("S4: 別の -u の引数の -u も外す形とみなす",
     "(?<!-u[ \\t])(?<!--unset[ \\t])", ""),
    ("S4: = の値の中の unset も外す形とみなす",
     "(?<![{_A}=-])unset[", "(?<![{_A}-])unset["),
    ("S4: unset を環境から外す形とみなさない",
     "(?<![{_A}=-])unset[ \\t]+", "(?<![{_A}=-])unsetNEVER[ \\t]+"),
    ("S4: env に渡す -u だけを並べた行を外す形とみなさない",
     'rf"^[ \\t]*(?:[\\"\']?(?:-u|--unset)', 'rf"^NEVER[ \\t]*(?:[\\"\']?(?:-u|--unset)'),
    ("S4: 行を \\ で続けた命令をつながない",
     '            if ln.rstrip().endswith("\\\\"):\n', "            if False:\n"),
    ("S4: 外す形が区切り（;）を越える",
     '    i = max(before.rfind(c) for c in ";&|")\n', "    i = -1\n"),
    ("S4: 値を使う .pop( も外す形とみなす",
     'rf"^[ \\t]*[{_A}.]{{1,128}}\\.pop\\(', 'rf"[{_A}.]{{1,128}}\\.pop\\('),
    # ---- 新しい形（recipes/case.yaml がある。 #171）
    ("新しい形: case.yaml があっても前の形として照らす",
     "self.new_form = CASE in self.files or os.path.lexists(root / CASE)", "self.new_form = False"),
    ("新しい形: 前の形の主の設定が無いと止める",
     "if not self.new_form and (IA_MAIN not in self.files", "if (IA_MAIN not in self.files"),
    ("新しい形: case.yaml がリンク・Git の外でも止めない",
     'if self.new_form and (CASE not in self.files or os.path.islink(root / "recipes")',
     'if False and (CASE not in self.files or os.path.islink(root / "recipes")'),
    ("新しい形: 前の形にも新しい置き場の照らしを当てる",
     "        if self.new_form:\n            self.new_places(docs)\n",
     "        if True:\n            self.new_places(docs)\n"),
    ("S1 新しい置き場: 決まりの外のファイルを通す",
     "            code = self.s1_new(rel)\n", "            code = None\n"),
    ("S1 新しい置き場: リンクを通す",
     '            return "S1-new-link"\n', "            return None\n"),
    ("S1 新しい置き場: 中身のある .gitkeep を読まない",
     '(name == ".gitkeep" and code is None)', '(name == ".gitkeep")'),
    ("新しい置き場: ファイルの名前の trig_ の語を照らさない",
     '            for part in rel.split("/")[1:]:\n', "            for part in ():\n"),
    ("新しい置き場: JSON を照らさない",
     "                docs[rel] = JsonDoc(rel, text)\n", "                pass\n"),
    ("新しい置き場: 決まりの外のファイルの中の値を照らさない",
     '                self.text_lines(rel, text, "L1-endpoint", "L1-name", self.findings)\n',
     "                pass\n"),
    ("製品の名前: ファイルの名前を照らさない",
     '            return "S3-product"\n', "            return None\n"),
    ("製品の名前: product を照らさない",
     'if s is not None and s in self.prof["products"]:', "if True:"),
    ("宣言: products を読まない",
     '    texts("products", ("products",), top.get("products"))\n', ""),
    ("宣言: 追加のファイルを config/・recipes/・machines/ の外にも置ける",
     "not path.startswith(EXTRA_DIRS)", "False"),
    ("S3 新しい形: case.name を照らさない",
     'if name in self.prof["project.profiles"]:', "if True:"),
    ("S2・S3 新しい形: case.yaml の profile を主の設定と同じに照らさない",
     "            self.ia_config(rel, view, True)\n", "            pass\n"),
    ("層 1 新しい形: profile の中を決まった場所で照らさない",
     "                    if self._known(rel, view, kind, path[1:], value, line):\n",
     "                    if False:\n"),
    ("層 1 新しい形: 機体の設定の PLC の宛先を照らさない",
     "            self._plc_endpoint(rel, view)\n", "            pass\n"),
    ("R: case.yaml の書かない鍵を通す",
     'if kind == "key" and _case_forbidden(path, doc.data):', "if False:"),
    ("R: 組み立て器が組む鍵（profile の根）を通す",
     '        return p[0] in CASE_ASSEMBLED or p[0] == "paths"\n', '        return p[0] == "paths"\n'),
    ("R: カメラの機体だけの鍵を通す",
     "return p[2] in CASE_CAMERA_KEYS or", "return False or"),
    ("R: sdk を値によらず断る",
     '(p[2] == "sdk" and _get(data, path) == "file")', '(p[2] == "sdk")'),
    ("R: PLC の宛先・NAS の置き場を通す",
     "    return p in CASE_MACHINE_PATHS\n", "    return False\n"),
    ("R: paths そのものを通す（値の型によらず断る。 #172 の A の MINOR-2）",
     ' or p[0] == "paths"\n', "\n"),
    ("R: 製品の書かない鍵を通す",
     'if kind == "key" and _product_forbidden(path):', "if False:"),
    ("R: capture の下の書かない鍵を見ない",
     'top = path[1:] if path[:1] == ("capture",) else path', "top = path"),
    ("R-opt: auto でも照合の型を通す",
     'if judge.get("opt_level") == "auto":', "if False:"),
    ("R-opt: auto でなくても照合の型を断る",
     'if judge.get("opt_level") == "auto":', "if True:"),
    ("M: Git にある kind: dev を通す",
     '            if kind == "dev":\n', "            if False:\n"),
    ("M: machine.kind を確かめない",
     "if not isinstance(kind, str) or kind not in MACHINE_KINDS:", "if False:"),
    ("見本: 照らさない",
     "            self.example(rel, doc)\n", "            pass\n"),
    ("見本: IPv4 を通す",
     "            if _IPV4.search(value):\n", "            if False:\n"),
    ("見本: 製造番号を通す",
     'if kind == "key" and value == "serial_number" and _get(doc.data, path) not in (None, ""):',
     "if False:"),
    ("見本: 絶対の道を通す",
     'if kind == "value" and _ABS_PATH.match(value) and not _placeholder_path(value):', "if False:"),
    ("見本: .. で外へ出る道を /path/to/ の形とみなす",
     "    n = posixpath.normpath(value)\n", "    n = value\n"),
    ("見本: Windows の絶対の道を見ない",
     r'_ABS_PATH = re.compile(r"/|\\|[A-Za-z]:[\\/]")', r'_ABS_PATH = re.compile(r"/|\\")'),
    ("S4 新しい形: 照らさない",
     "                    self.refs_new(rel, text)\n", "                    pass\n"),
    ("S4 新しい形: 前の形にも当てる",
     "                if self.new_form:\n                    self.refs_new",
     "                if True:\n                    self.refs_new"),
    ("S4 新しい形: versions.yaml の注釈の行も照らす",
     '            if rel in S4_FILES and ln.lstrip().startswith("#"):\n', "            if False:\n"),
    ("S4 新しい形: versions.dev.yaml を見ない",
     "bad = bool(_DEV_PIN.search(ln))", "bad = False"),
    ("S4 新しい形: 見本を指すのを見ない",
     "(target == DEV_EXAMPLE or target not in self.files)", "(target not in self.files)"),
    ("S4 新しい形: Git に無い機体の設定を見ない",
     "(target == DEV_EXAMPLE or target not in self.files)", "(target == DEV_EXAMPLE)"),
    ("YAML: 同じ鍵を通す",
     "                if kn.value in names:\n", "                if False:\n"),
    ("YAML: 別名を通す",
     "        if id(node) in visited:\n", "        if False:\n"),
    ("YAML: 数の鍵を通す",
     'if not isinstance(kn, yaml.ScalarNode) or kn.tag != _YAML_TAG + "str":',
     "if not isinstance(kn, yaml.ScalarNode):"),
    ("YAML: << を、 文字でない鍵として断る（誤りの文が違う）",
     'if kn.tag == _YAML_TAG + "merge":', "if False:"),
    ("YAML: 知らない型の対応表を通す",
     'if node.tag != _YAML_TAG + "map":', "if False:"),
    ("YAML: 知らない型の値を通す",
     "            if node.tag not in _YAML_SCALARS:\n", "            if False:\n"),
    ("YAML: 数を読んだ型で照らす（先頭の 0 が消える）",
     "value = node.value if node.tag in _YAML_AS_WRITTEN else loader.construct_object(node)",
     "value = loader.construct_object(node)"),
    # ---- #172 のレビューの直し
    ("S4 新しい形: 前に道のある機体の設定の参照を拾わない（B-1）",
     'rf"(?<![{_A}.-])(machines/', 'rf"(?<![{_A}./-])(machines/'),
    ("新しい形: case.yaml が無いのに新しい置き場があっても前の形として通す（B-2）",
     "if not self.new_form and any(_stray_new_place(root, f) for f in self.files):", "if False:"),
    ("新しい形: case.yaml が無いとき、 説明の .md と空の .gitkeep も新しい置き場とみなす",
     "    if parts[-1].endswith(\".md\") or (parts[-1] == \".gitkeep\" and not os.path.islink(p)",
     "    if False and (parts[-1] == \".gitkeep\" and not os.path.islink(p)"),
    ("新しい形: case.yaml が無いとき、 中身のある .gitkeep を除く",
     "and p.is_file() and p.stat().st_size == 0):", "and p.is_file()):"),
    ("新しい形: case.yaml の書き違いを見ない",
     'return len(parts) >= 2 and parts[0] == "recipes" and parts[1].casefold().split(".")[0] == "case"',
     "return False"),
    ("新しい形: case.yaml の書き違いの大文字を見ない",
     'parts[1].casefold().split(".")[0] == "case"', 'parts[1].split(".")[0] == "case"'),
    ("M-key: 機体に書けない鍵を通す（B-3）",
     "and not _machine_allowed(path[1:], _get(doc.data, path), dev)):", "and False):"),
    ("M-key: 開発の機体だけが書ける鍵を、 どの機体でも許す",
     "        dev = rel == DEV_EXAMPLE or kind == \"dev\"\n", "        dev = True\n"),
    ("M-key: 見本にも開発の機体だけが書ける鍵を許さない",
     "        dev = rel == DEV_EXAMPLE or kind == \"dev\"\n", "        dev = kind == \"dev\" and rel != DEV_EXAMPLE\n"),
    ("M-key: sdk を値によらず許す",
     '(p[2] == "sdk" and value == "file")', '(p[2] == "sdk")'),
    ("M-key: paths.work を許す",
     'return len(p) == 2 and p[1] != "work"', "return len(p) == 2"),
    ("M-key: 機体の根の鍵を何でも許す",
     "        return p[0] in MACHINE_ROOT_KEYS\n", "        return True\n"),
    ("M-key: 機体で上書きできるカメラの鍵を許さない",
     "        if p[2] in MACHINE_CAMERA_KEYS:\n", "        if False:\n"),
    ("M-key: plc・nas の決まった鍵を許さない",
     "    return p in MACHINE_PATHS\n", "    return False\n"),
    ("見本: machine.kind を照らさない（B-4）",
     '            if kind != "dev":\n', "            if False:\n"),
    ("形: 上の段が対応表でなくても止めない（A の MINOR-2）",
     "        if bad:\n            raise Stop(\"config-field\", f\"profile.{_field_text(bad[0])}",
     "        if False:\n            raise Stop(\"config-field\", f\"profile.{_field_text(bad[0])}"),
    ("形: カメラの役ごとの値を見ない",
     "            bad += [(\"camera\", r) for r, v in cams.items()", "            bad += [(\"camera\", r) for r, v in ()"),
    ("形: 止めるときに欄を伏せない",
     'f"profile.{_field_text(bad[0])} を対応表で', 'f"profile.{\'.\'.join(map(str, bad[0]))} を対応表で'),
    ("形: 機体の設定の上の段を見ない",
     "        self._shape(rel, doc.data.get(\"profile\"), MACHINE_SHAPES)\n", ""),
    # ---- 前の形で主の設定が無いとき、 開発の機体だけの設定を主の設定の代わりに照らす（#182）
    ("#182: 開発の機体だけの設定を主の設定の代わりにしない（直す前）",
     "            self.dev_main = self._dev_mains() if absent else []\n", "            self.dev_main = []\n"),
    ("#182: 主の設定の代わりにする開発の設定を、 主の設定と同じに照らさない",
     "self.ia_config(rel, doc, rel == IA_MAIN or rel in self.dev_main)", "self.ia_config(rel, doc, rel == IA_MAIN)"),
    ("#182: config/ に説明のほかがあっても、 開発の設定で代える",
     "            if os.path.islink(p) or not (name.endswith(\".md\")",
     "            if False and (name.endswith(\".md\")"),
    ("#182: Git の外に主の設定があっても、 開発の設定で代える",
     "        if os.path.lexists(root / IA_MAIN):\n", "        if False:\n"),
    ("#182: 決まりの外の開発の設定のファイルも、 主の設定の代わりにする",
     "and self.s6(rel) is None and (root / rel).is_file()]", "and (root / rel).is_file()]"),
    ("#182: 主の設定の代わりにする開発の設定で、 宣言で使う PLC を切るのを断る",
     "elif main and not dev and enabled is not True:", "elif main and enabled is not True:"),
    ("#182: 主の設定の代わりにする開発の設定に節が無くても止めない",
     "            if main and cap not in d:\n", "            if False:\n"),
]


class MutationTest(unittest.TestCase):
    """**検査の守りを 1 つ壊すと、 どれかの場面が落ちること。** 落ちなければ、
    その守りは試験で確かめられていない。"""

    def _all(self, mod) -> "dict[str, list[str]]":
        return {sc.name: problems(mod, sc) for sc in SCENARIOS}

    def _first_failure(self, mod) -> "str | None":
        """落ちる場面を 1 つ見つけたら止める（確かめるのは「どれかが落ちる」こと。 場面と変異の
        数の積で時間が増え、 CI の job の時間の上限に近づいたため。 #171）。"""
        for sc in SCENARIOS:
            if problems(mod, sc):
                return sc.name
        return None

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
                    failed = self._first_failure(copy(f"m{i:02d}", src.replace(old, new)))
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

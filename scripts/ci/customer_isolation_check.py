#!/usr/bin/env python3
"""**顧客リポジトリの設定に、 別の案件の値が混ざっていないか**を、 その案件
自身の宣言（リポジトリの根の `customer-profile.yaml`）と照らして確かめる。

起きたこと: 顧客リポジトリの最初の登録で、 撮像側の別のブランチの設定を
そのまま写した。 別の案件の本番の構成のファイルが 1 つと、 別の案件の PLC の
表（`plc.enabled` は false）が入った。 混ざったのは名前ではなく値なので、
名前の検査（`customer_data_check.py`）では見つからない。 配る道具は知らない
名前のファイルを黙って配らず、 PLC を使わない案件は PLC の表を読まないので、
据え付けの確かめ（`--dry-run`）も通った。

**「この案件にあってよいもの」を案件自身が宣言し、 それ以外を落とす。** ほかの
顧客の値も、 顧客の名前の登録表も、 secret も使わない。 PR の CI で、 読むだけの
権限で動かせる。

    python scripts/ci/customer_isolation_check.py                  # CI_ROOT か今のフォルダ
    python scripts/ci/customer_isolation_check.py --root <顧客リポジトリ>
    python scripts/ci/customer_isolation_check.py --base-ref <基準の commit>  # 宣言の増減も出す
    python scripts/ci/customer_isolation_check.py --report-only    # 混入の疑いがあっても 0 で終える

終了コード（CI ではどちらも不合格。 記録の最後の行で区別できる）:

    0  合格
    1  混入の疑い（宣言の外の値・ファイル・構成）
    2  宣言・設定・検査の誤り（宣言が無い・読めない・資格情報らしい鍵がある、
       設定の JSON が読めない、 PyYAML が無い など）。 **照らせないときは
       合格にしない**

見るもの:

    層 1  決まった場所から識別子を取り出して、 宣言と照らす（カメラの IP と
          製造番号、 PLC の宛先と番地、 トリガ）。 補いとして、 決まった場所の
          外の IPv4 と `trig_` で始まる語も照らす
    S1    config/ の下のファイルが、 Core が読む決まった名前か、 宣言した
          追加のファイルか（説明の .md と空の .gitkeep は除く）。 config/pdm/ は、
          配る道具が同じ並びのまま配る .json と .txt（defaults/ は除く）。 その
          中の面ごとの設定の置き場は、 ファイルの名前が宣言の面か（S3）も見る
    S2    PLC・NAS・AI を使うかが宣言と同じか。 PLC を使わないなら
          `plc` は `{"enabled": false}` だけ
    S3    構成の名前（`_profile`）・flow・カメラ・面が宣言の中か
    S4    deploy/ と versions.yaml が触れる設定のファイルが、 実在して
          許されているか。 deploy/ の中の値は **参考として出すだけ**
          （強制するかは、 既存の案件で報告だけの形で流した結果を見て決める）

一般の値は宣言しなくてよい: `127.` と `255.` で始まるもの、 `0.0.0.0`、
Core の鍵 `trig_bit`・`trig_ready`。 **文書用の網（192.0.2.x など）は除かない。**
見本の値で試験をするので、 除くと試験が何も確かめなくなる。

**見つけた値は出力しない。** 出すのは種類・ファイル・行・欄だけ。 欄の中の
決まっていない名前は `<*>`、 ファイルの道の中の決まっていない名前は
`<伏せ#n>`（1 回の実行の中で、 同じ名前に同じ番号）。 CI の記録は外から
見えることがあるので、 その案件自身の値も記録に広げない。

**宣言の増え方**: `--base-ref` を渡すと、 基準の commit の宣言と比べて、
種類ごとの増減の **数だけ** を出す。 増えていれば `PROFILE EXPANSION DETECTED`。
設定と宣言の両方へ同じ誤った値を足すと、 照合では見つからない。 その形を
レビューで目立たせるためのもので、 これだけでは不合格にしない。

**PR の中身は、 データとして読むだけで実行しない。** 使う git の命令は
`ls-files` と `cat-file` だけ。 宣言は別名（`*`）・同じ鍵の重複・資格情報らしい
鍵と値を断り、 大きさの上限を超えるものは読まない。

PyYAML が要る（`python -m pip install pyyaml`）。 無ければ終了コード 2 で止める。
"""
from __future__ import annotations

import argparse
import bisect
import ipaddress
import json
import os
import posixpath
import re
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import basic_checks                                        # noqa: E402

#: 宣言のファイル（顧客リポジトリの根）。
PROFILE = "customer-profile.yaml"
#: 知っている宣言の形。 **知らない値は黙って受け付けない。**
SCHEMA_VERSIONS = (1,)

EXIT_PASS, EXIT_VIOLATION, EXIT_ERROR = 0, 1, 2

#: 大きさの上限。 宣言は人が読む大きさで足りる。 設定は機械チェックの上限と同じ。
MAX_PROFILE_BYTES = 256 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
#: 入れ子の深さの上限（深すぎる宣言・設定で止まらないように）。
MAX_DEPTH = 64
#: 宣言の 1 つの文字列の長さの上限。
MAX_TEXT = 512
#: 網で許すときの、 いちばん広い網。 これより広い網は断る。
MIN_PREFIX = 24
#: 出力する所見の行の上限（数は必ず全部出す）。
MAX_SHOWN = 200

#: 撮像側（Image-Acquisition-PDM）の設定の置き場。 配る道具（撮像側の
#: `scripts/setup_customer.sh`）が読む名前だけを許す。 ほかの名前は、 配る道具が
#: **黙って配らない**ので、 置いてあっても誰も気づかない。
IA_DIR = "config/image-acquisition/"
IA_MAIN = IA_DIR + "config.json"
IA_FIXED = ("config.json", "pdm_guard_policy.json", "recipe.json", "machine.json")
#: 学習・評価側（PDM）の設定の置き場。 配る道具は、 ここの .json と .txt を同じ
#: 並びのまま配る（`defaults/` と `.` で始まるものは配らない）。
PDM_DIR = "config/pdm/"
PDM_SUFFIXES = (".json", ".txt")
#: 面ごとの設定の置き場。 ファイルの名前は `<面>` か `<面>__region...`。 面は、
#: 面の名前か、 その末尾の `_<数字>` を落とした基の名前（配る道具と同じ）。
PDM_FACE_DIRS = ("adaptive_mask", "align", "bbox_acquire", "good_pool", "region_split")

#: Core の PLC の表の鍵（トリガごとの信号の名前）。 名前ではない。
CORE_TRIG_KEYS = frozenset({"trig_bit", "trig_ready"})
#: 宣言に置いてはいけない鍵（資格情報）。 大文字と小文字、 `_` や `-` を除いて比べる。
CREDENTIAL_WORDS = ("password", "passwd", "passphrase", "secret", "token",
                    "credential", "privatekey", "apikey", "accesskey")

#: 出力にそのまま出す欄の名前（この検査が読む Core の設定の鍵）。 ほかは `<*>`。
SHOWN_KEYS = frozenset({
    "_profile", "flow", "camera", "shot", "face_dict", "plc", "nas", "ai",
    "enabled", "address", "host", "port", "ip_address", "serial_number",
    "triggers", "shot_request", "result_slot", "data", "start", "end", "bit",
    *CORE_TRIG_KEYS})
#: 出力にそのまま出すファイルの道の部品。 ほかは `<伏せ#n>`。
SHOWN_PARTS = frozenset({
    "config", "image-acquisition", "pdm", "deploy", "defaults", "versions.yaml",
    PROFILE, "README.md", ".gitkeep", *IA_FIXED, *PDM_FACE_DIRS})
SHOWN_SUFFIXES = (".json", ".txt", ".md", ".yaml", ".yml", ".service", ".sh",
                  ".py", ".conf", ".ini", ".toml", ".cfg", ".env")

#: 所見の種類。 `NG [<符号>] <説明>` の形で出す（試験は符号で見る）。
WHAT = {
    "S1-file": "決まりの外の設定のファイル（Core が読む名前でも、 宣言した追加のファイルでもない）",
    "S1-link": "config/ の下のリンク（読まずに断る）",
    "S1-defaults": "config/pdm/defaults/ は Core が配る既定の置き場（案件の値を置かない）",
    "S2-plc": 'PLC を使わない宣言なのに、 plc が {"enabled": false} だけではない（PLC の表が残っている）',
    "S2-plc-enabled": "PLC を使う宣言なのに、 plc.enabled が true ではない",
    "S2-nas": "nas.enabled が宣言（capabilities.nas）と違う",
    "S2-ai": "ai.enabled が宣言（capabilities.ai）と違う",
    "S3-profile": "宣言に無い構成の名前（_profile）",
    "S3-flow": "flow が宣言（project.flow）と違う",
    "S3-camera": "宣言に無いカメラの id",
    "S3-face": "宣言に無い面の名前（face_dict の鍵）",
    "S3-face-file": "宣言に無い面の名前の、 面ごとの設定のファイル",
    "S4-missing": "参照している設定のファイルが無い",
    "S4-not-allowed": "参照している設定のファイルが、 許されていない",
    "L1-endpoint": "宣言に無い宛先（IP・host）",
    "L1-serial": "宣言に無い製造番号",
    "L1-plc-endpoint": "宣言に無い PLC の宛先",
    "L1-plc-address": "宣言に無い PLC の番地",
    "L1-trigger": "宣言に無いトリガの識別子",
    "L1-name": "宣言に無いトリガ・面の名前らしき語",
}
#: 参考として出すもの（不合格にしない）。
NOTE = {
    "deploy-endpoint": "deploy/ の中の、 宣言に無い IP らしきもの",
    "deploy-name": "deploy/ の中の、 宣言に無いトリガ・面の名前らしき語",
}

_A = "A-Za-z0-9_"
#: IPv4 らしき並び（後ろに `/<数>` があれば網）。 5 つ以上の数の並び（版の番号など）は拾わない。
_IPV4 = re.compile(r"(?<![0-9.])((?:[0-9]{1,3}\.){3}[0-9]{1,3})(?:/([0-9]{1,2}))?(?!\.?[0-9])")
_TRIG = re.compile(rf"(?<![{_A}])trig_[{_A}]*[A-Za-z0-9]")
#: deploy/ と versions.yaml の中の、 設定のファイルへの参照（道と、 config で始まる名前）。
_REF_PATH = re.compile(rf"(?<![{_A}.-])config/[\w./-]*?\.(?:json|txt)(?![{_A}-]|\.[{_A}])")
_REF_NAME = re.compile(rf"(?<![{_A}./-])config(?:\.[\w-]+)*\.json(?![{_A}-]|\.[{_A}])")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                   r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*")
_HEX_REF = re.compile(r"[0-9a-f]{7,40}")
_JSON_SCALAR = re.compile(
    r'"(?:[^"\\]|\\.)*"|-?Infinity|NaN|true|false|null|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')


class Stop(Exception):
    """照らせないので止める（終了コード 2）。

    **文に値を入れない**（符号と決まった言葉と行だけ）。 ファイルは `rel` で渡し、
    出すときに道を伏せる。
    """

    def __init__(self, code: str, message: str, rel: "str | None" = None) -> None:
        super().__init__(code)
        self.code, self.message, self.rel = code, message, rel


class Finding:
    """所見 1 つ。 **値を持たない**（道と欄は、 出すときに伏せる）。"""

    __slots__ = ("code", "rel", "line", "field")

    def __init__(self, code: str, rel: str, line: "int | None" = None,
                 field: "tuple | None" = None) -> None:
        self.code, self.rel, self.line, self.field = code, rel, line, field

    def key(self) -> tuple:
        return (self.rel, self.line or 0, self.code, _field_text(self.field))


# ---------------------------------------------------------------- 宣言

#: 宣言の種類（宣言の増減もこの単位で数える）。
CATEGORIES = (
    "project.profiles", "equipment.cameras", "equipment.cameras.ip",
    "equipment.cameras.serial", "endpoints", "endpoints.network",
    "plc.endpoints", "plc.addresses", "triggers", "faces",
    "config_files.extra", "extensions")

_TOP_KEYS = ("schema_version", "project", "capabilities", "equipment", "endpoints",
             "plc", "triggers", "faces", "config_files", "extensions")
#: 誤りの文に出してよい、 宣言の決まった鍵（知らない鍵は出さない）。
_SCHEMA_WORDS = frozenset(_TOP_KEYS) | {"flow", "profiles", "nas", "ai", "cameras",
                                        "id", "ip", "serial", "network", "reason",
                                        "addresses", "extra", "path"}


class Profile:
    """宣言を読んだもの。 種類ごとの値の集まり（比べる形に直したもの）。"""

    def __init__(self) -> None:
        self.flow = ""
        self.caps: "dict[str, bool]" = {}
        self.sets: "dict[str, set]" = {k: set() for k in CATEGORIES}
        self.networks: "list[ipaddress.IPv4Network]" = []

    def __getitem__(self, name: str) -> set:
        return self.sets[name]

    def endpoints(self) -> set:
        """宛先として許すもの（ちょうどの値）。 PLC の宛先の host も含む。"""
        return (self["equipment.cameras.ip"] | self["endpoints"]
                | {e.rsplit(":", 1)[0] for e in self["plc.endpoints"]})

    def names(self) -> set:
        """`trig_` で始まる語として許すもの（トリガ・面と、 その基の名前）。"""
        n = self["triggers"] | self["faces"]
        return n | {_base(x) for x in n}

    def face_names(self) -> set:
        """面ごとの設定のファイルの名前として許すもの（面と、 その基の名前）。"""
        return self["faces"] | {_base(x) for x in self["faces"]}


def _base(name: str) -> str:
    """基の面の名前（末尾の `_<数字>` を落とす。 配る道具と PDM と同じ）。"""
    return re.sub(r"_[0-9]+$", "", name)


def _norm_ipv4(s: str) -> "str | None":
    parts = s.split(".")
    if len(parts) != 4 or not all(p.isascii() and p.isdigit() and len(p) <= 3
                                  for p in parts):
        return None
    nums = [int(p) for p in parts]
    return None if any(n > 255 for n in nums) else ".".join(map(str, nums))


def _norm_host(s: str) -> "str | None":
    """IPv4 か host 名を、 比べる形に直す。 どちらでもなければ None。"""
    if re.fullmatch(r"[0-9.]+", s):
        return _norm_ipv4(s)
    return s.lower() if len(s) <= 253 and _HOST.fullmatch(s) else None


def _norm_endpoint(s: str) -> "str | None":
    """`host:port`（port は 1〜65535）を、 比べる形に直す。"""
    host, sep, port = s.rpartition(":")
    if not sep or not (port.isascii() and port.isdigit()) or not 0 < int(port) < 65536:
        return None
    h = _norm_host(host)
    return None if h is None else f"{h}:{int(port)}"


def _generic_ip(ip: str) -> bool:
    """宣言しなくてよい IP（ループバック・すべての宛先・網の形の値）。 文書用の網は含めない。"""
    return ip.startswith(("127.", "255.")) or ip == "0.0.0.0"


def _credential_key(key: str) -> bool:
    k = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(w in k for w in CREDENTIAL_WORDS)


def _secret_value(value: str) -> bool:
    """資格情報らしい値（機械チェックの秘密情報の形と、 秘密鍵の見出し）。"""
    if "PRIVATE KEY" in value.upper():
        return True
    return any(p.search(value) for _, p in basic_checks.SECRET_PATTERNS)


def _yaml():
    """**PyYAML を暗黙の前提にしない。** 無ければ止める（合格にしない）。"""
    try:
        import yaml
    except ImportError:
        raise Stop("yaml-missing",
                   "PyYAML が無いので宣言を読めません。 `python -m pip install pyyaml`"
                   " を実行してください") from None
    return yaml


def _compose(text: str, yaml) -> "tuple[object, dict]":
    """宣言を読む。 別名・`<<`・同じ鍵の重複・資格情報らしい鍵と値を断る。

    戻り値は (値, 道ごとの行)。 道は鍵と番号の組（例: `("endpoints", 2)`）。
    """
    class _Loader(yaml.SafeLoader):
        def compose_node(self, parent, index):                     # noqa: N802
            if self.check_event(yaml.AliasEvent):
                line = self.peek_event().start_mark.line + 1
                raise Stop("profile-yaml", f"別名（*）は使えません（{line} 行目）")
            return super().compose_node(parent, index)

    loader = _Loader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            raise Stop("profile-schema", "宣言が空です")
        lines: "dict[tuple, int]" = {}
        _inspect(node, (), lines, yaml, 0)
        data = loader.construct_document(node)
    except Stop:
        raise
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
        where = f"（{mark.line + 1} 行目あたり）" if mark is not None else ""
        raise Stop("profile-yaml", f"宣言を YAML として読めません{where}") from None
    except RecursionError:
        raise Stop("profile-yaml", "宣言の入れ子が深すぎます") from None
    finally:
        loader.dispose()
    return data, lines


def _inspect(node, path: tuple, lines: dict, yaml, depth: int) -> None:
    """node の木をたどって、 形の誤りと資格情報を探す（値は出さない）。"""
    if depth > MAX_DEPTH:
        raise Stop("profile-yaml", "宣言の入れ子が深すぎます")
    line = node.start_mark.line + 1
    lines.setdefault(path, line)
    if isinstance(node, yaml.MappingNode):
        seen = set()
        for k, v in node.value:
            kl = k.start_mark.line + 1
            if k.tag == "tag:yaml.org,2002:merge":
                raise Stop("profile-yaml", f"`<<` は使えません（{kl} 行目）")
            if not isinstance(k, yaml.ScalarNode):
                raise Stop("profile-yaml", f"鍵は文字にしてください（{kl} 行目）")
            if _credential_key(k.value) or _secret_value(k.value):
                raise Stop("profile-credential",
                           f"資格情報らしい鍵があります（{kl} 行目）。 宣言に資格情報を"
                           "書かないでください（宣言は識別子だけを持つ）")
            if k.value in seen:
                raise Stop("profile-yaml", f"同じ鍵が 2 回あります（{kl} 行目）")
            seen.add(k.value)
            lines.setdefault(path + (k.value,), kl)
            _inspect(v, path + (k.value,), lines, yaml, depth + 1)
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            _inspect(item, path + (i,), lines, yaml, depth + 1)
    elif _secret_value(node.value):
        raise Stop("profile-credential",
                   f"資格情報らしい値があります（{line} 行目）。 宣言に資格情報を書かないでください")


def parse_profile(text: str, yaml) -> Profile:
    """宣言を読んで確かめる。 誤りは `Stop`（文に値を入れない）。"""
    data, lines = _compose(text, yaml)

    def bad(path: tuple, what: str) -> NoReturn:
        line = lines.get(path)
        raise Stop("profile-schema",
                   f"{_schema_path(path)}: {what}" + (f"（{line} 行目）" if line else ""))

    def mapping(path: tuple, v, keys: tuple, required: tuple = ()) -> dict:
        if not isinstance(v, dict):
            bad(path, "対応表（鍵: 値）にしてください")
        for k in v:
            if k not in keys:
                bad(path + (k,), "知らない鍵です")
        for k in required:
            if k not in v:
                bad(path, f"{k} がありません")
        return v

    def listed(path: tuple, v) -> list:
        if v is None:
            return []
        if not isinstance(v, list):
            bad(path, "一覧（- で始まる行）にしてください")
        return v

    def text_(path: tuple, v) -> str:
        if not isinstance(v, str) or not v.strip():
            bad(path, "空でない文字にしてください")
        if len(v) > MAX_TEXT or any(ord(c) < 0x20 or ord(c) == 0x7F for c in v):
            bad(path, "長すぎるか、 制御文字を含みます")
        return v

    def add(cat: str, path: tuple, value: str) -> None:
        if value in p[cat]:
            bad(path, "同じものが 2 回あります")
        p[cat].add(value)

    def texts(cat: str, path: tuple, v, norm=None, what: str = "") -> None:
        for i, item in enumerate(listed(path, v)):
            if cat == "plc.addresses" and isinstance(item, int) and not isinstance(item, bool):
                item = str(item)                     # YAML では引用符が無いと数になる
            s = text_(path + (i,), item)
            if norm is not None:
                s = norm(s)
                if s is None:
                    bad(path + (i,), what)
            add(cat, path + (i,), s)

    p = Profile()
    top = mapping((), data, _TOP_KEYS, ("schema_version", "project", "capabilities"))
    sv = top["schema_version"]
    if isinstance(sv, bool) or sv not in SCHEMA_VERSIONS:
        bad(("schema_version",), "知らない形です（知っているのは "
            + ", ".join(map(str, SCHEMA_VERSIONS)) + "）")

    proj = mapping(("project",), top["project"], ("flow", "profiles"), ("flow",))
    p.flow = text_(("project", "flow"), proj["flow"])
    texts("project.profiles", ("project", "profiles"), proj.get("profiles"))

    caps = mapping(("capabilities",), top["capabilities"], ("plc", "nas", "ai"),
                   ("plc", "nas", "ai"))
    for k in ("plc", "nas", "ai"):
        if not isinstance(caps[k], bool):
            bad(("capabilities", k), "true か false にしてください（推測しません）")
        p.caps[k] = caps[k]

    eq = mapping(("equipment",), top.get("equipment") or {}, ("cameras",))
    for i, c in enumerate(listed(("equipment", "cameras"), eq.get("cameras"))):
        at = ("equipment", "cameras", i)
        c = mapping(at, c, ("id", "ip", "serial"), ("id",))
        add("equipment.cameras", at + ("id",), text_(at + ("id",), c["id"]))
        if c.get("ip") is not None:
            ip = _norm_ipv4(text_(at + ("ip",), c["ip"]))
            if ip is None:
                bad(at + ("ip",), "ちょうどの IPv4 にしてください（網は endpoints に書く）")
            p["equipment.cameras.ip"].add(ip)
        if c.get("serial") is not None:
            add("equipment.cameras.serial", at + ("serial",), text_(at + ("serial",), c["serial"]))

    for i, e in enumerate(listed(("endpoints",), top.get("endpoints"))):
        at = ("endpoints", i)
        if isinstance(e, dict):
            # **網で許すのは、 本当に要るときだけ。** 理由を書かせ、 広すぎる網は断る。
            m = mapping(at, e, ("network", "reason"), ("network", "reason"))
            text_(at + ("reason",), m["reason"])
            try:
                net = ipaddress.IPv4Network(text_(at + ("network",), m["network"]))
            except ValueError:
                bad(at + ("network",), "IPv4 の網（例: 192.0.2.0/28。 端の数は 0）にしてください")
            if net.prefixlen < MIN_PREFIX:
                bad(at + ("network",), f"網が広すぎます（/{MIN_PREFIX} より狭くしてください）")
            add("endpoints.network", at + ("network",), str(net))
            p.networks.append(net)
            continue
        s = text_(at, e)
        if "/" in s:
            bad(at, "網は network: と reason: の対応表で書いてください")
        h = _norm_host(s)
        if h is None:
            bad(at, "ちょうどの IPv4 か host 名にしてください")
        add("endpoints", at, h)

    plc = mapping(("plc",), top.get("plc") or {}, ("endpoints", "addresses"))
    texts("plc.endpoints", ("plc", "endpoints"), plc.get("endpoints"),
          _norm_endpoint, "host:port の形にしてください")
    texts("plc.addresses", ("plc", "addresses"), plc.get("addresses"))
    if not p.caps["plc"] and (p["plc.endpoints"] or p["plc.addresses"]):
        bad(("plc",), "capabilities.plc が false なのに、 PLC の宛先か番地があります")

    texts("triggers", ("triggers",), top.get("triggers"))
    texts("faces", ("faces",), top.get("faces"))
    texts("extensions", ("extensions",), top.get("extensions"))

    cf = mapping(("config_files",), top.get("config_files") or {}, ("extra",))
    for i, x in enumerate(listed(("config_files", "extra"), cf.get("extra"))):
        at = ("config_files", "extra", i)
        x = mapping(at, x, ("path", "reason"), ("path", "reason"))
        text_(at + ("reason",), x["reason"])
        path = text_(at + ("path",), x["path"])
        if (posixpath.normpath(path) != path or not path.startswith("config/")
                or "\\" in path or ".." in path.split("/")):
            bad(at + ("path",), "config/ の下の、 そのままの道（.. や // を使わない）にしてください")
        add("config_files.extra", at + ("path",), path)
    return p


def _schema_path(path: tuple) -> str:
    """宣言の中の場所。 **鍵は宣言の決まった名前だけを出す**（知らない鍵は出さない）。"""
    out = []
    for x in path:
        if isinstance(x, int):
            out.append(f"[{x}]")
        else:
            out.append(("." if out else "") + (x if x in _SCHEMA_WORDS else "<*>"))
    return "".join(out) or "（宣言の全体）"


# ---------------------------------------------------------------- 設定のファイル


def list_files(root: Path) -> "list[str]":
    """config/・deploy/・versions.yaml の、 追跡しているもの + まだ add していない新しいもの。

    git のリポジトリでなければ、 フォルダをたどる（リンクはたどらない）。
    """
    specs = ("config", "deploy", "versions.yaml")
    if (root / ".git").exists():
        seen: "set[bytes]" = set()
        for extra in ((), ("--others", "--exclude-standard")):
            r = subprocess.run(["git", "ls-files", "-z", *extra, "--", *specs],
                               cwd=root, capture_output=True)
            if r.returncode != 0:
                raise Stop("git", "git の一覧を読めません")
            seen.update(raw for raw in r.stdout.split(b"\0") if raw)
        return sorted(os.fsdecode(raw) for raw in seen)
    out = ["versions.yaml"] if (root / "versions.yaml").exists() else []
    for top in ("config", "deploy"):
        for d, dirs, files in os.walk(root / top):
            rel_d = Path(d).relative_to(root).as_posix()
            links = [n for n in dirs if os.path.islink(os.path.join(d, n))]
            out.extend(f"{rel_d}/{n}" for n in links + files)
            dirs[:] = [n for n in dirs if n not in links]
    return sorted(out)


def _read(root: Path, rel: str) -> str:
    p = root / rel
    try:
        if p.stat().st_size > MAX_FILE_BYTES:
            raise Stop("config-size", "大きすぎて読めません", rel)
        return p.read_bytes().decode("utf-8-sig", "replace")
    except OSError:
        raise Stop("config-read", "ファイルを読めません", rel) from None


class _Pairs(list):
    """JSON の対応表を、 鍵の重複も含めて並びのまま持つ。"""


class JsonDoc:
    """設定の JSON 1 つ。 構造（dict）と、 鍵と値の 1 つずつの行。"""

    def __init__(self, rel: str, text: str) -> None:
        try:
            self.data = json.loads(text)
            pairs = json.loads(text, object_pairs_hook=_Pairs)
        except json.JSONDecodeError as e:
            raise Stop("config-json", f"設定の JSON を読めません（{e.lineno} 行目）", rel) from None
        except RecursionError:
            raise Stop("config-json", "設定の JSON の入れ子が深すぎます", rel) from None
        nl = [m.start() for m in re.finditer("\n", text)]
        it = iter([(m.group(0), bisect.bisect_left(nl, m.start()) + 1)
                   for m in _JSON_SCALAR.finditer(text)])
        #: (種類, 道, 値, 行)。 種類は "key" か "value"。 道は鍵と番号の組。
        self.entries: "list[tuple[str, tuple, object, int | None]]" = []
        aligned = self._walk(pairs, (), it, 0) and next(it, None) is None
        if not aligned:
            # 行を取り違えるくらいなら、 行を出さない（値の照合はそのまま行う）。
            self.entries = [(k, p, v, None) for k, p, v, _ in self.entries]

    def _walk(self, node, path: tuple, it, depth: int) -> bool:
        """並びのまま鍵と値を並べ、 字句の列と行を合わせる。 合わなければ False。"""
        if depth > MAX_DEPTH:
            raise Stop("config-json", "設定の JSON の入れ子が深すぎます")
        ok = True
        if isinstance(node, _Pairs):
            for k, v in node:
                tok = next(it, None)
                ok = ok and tok is not None and tok[0].startswith('"')
                self.entries.append(("key", path + (k,), k, tok[1] if tok else None))
                ok = self._walk(v, path + (k,), it, depth + 1) and ok
        elif isinstance(node, list):
            for i, v in enumerate(node):
                ok = self._walk(v, path + (i,), it, depth + 1) and ok
        else:
            tok = next(it, None)
            ok = tok is not None and tok[0].startswith('"') == isinstance(node, str)
            self.entries.append(("value", path, node, tok[1] if tok else None))
        return ok

    def line(self, kind: str, path: tuple) -> "int | None":
        for k, p, _, ln in self.entries:
            if k == kind and p == path:
                return ln
        return None


def _is_ia_config(rel: str, data) -> bool:
    """撮像側の案件の設定らしいもの（決まった場所の照合と S2・S3 を当てる）。

    置き場所によらない（別の場所へ写した構成も見る）。
    """
    return rel == IA_MAIN or (isinstance(data, dict)
                              and any(k in data for k in ("camera", "shot", "face_dict", "plc")))


def _is_trigger_entry(v) -> bool:
    """`shot_request` の中の 1 つが、 トリガの項目か（信号の集まり）。

    信号は `{"bit": .., "data": ..}`、 トリガの項目は信号の集まり
    （`{"<信号>": {"bit": .., "data": ..}, ..}`）。 **信号の名前の一覧を持たない**
    （Core の内部の名前を写すと、 Core を直したときに食い違う）。
    """
    return (isinstance(v, dict) and bool(v) and "bit" not in v and "data" not in v
            and all(isinstance(x, dict) for x in v.values()))


def _scalar_text(v) -> "str | None":
    """照らす値の文字（数で書かれた番号・番地も文字にする）。 空や真偽は None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return str(v)
    return v if isinstance(v, str) and v else None


def _get(data, path: tuple):
    for k in path:
        if isinstance(data, dict) and k in data:
            data = data[k]
        elif isinstance(data, list) and isinstance(k, int) and k < len(data):
            data = data[k]
        else:
            return None
    return data


class Checker:
    """1 つのリポジトリを照らす。"""

    def __init__(self, root: Path, prof: Profile) -> None:
        self.root, self.prof = root, prof
        self.findings: "list[Finding]" = []
        self.notes: "list[Finding]" = []
        self.used: "dict[str, set]" = {k: set() for k in CATEGORIES}
        self.files: "list[str]" = []
        self.allowed: "set[str]" = set()
        self.counts = {"config": 0, "refs": 0}
        self._endpoints = prof.endpoints()
        self._names = prof.names()
        self._faces = prof.face_names()

    def add(self, code: str, rel: str, line=None, field=None) -> None:
        self.findings.append(Finding(code, rel, line, field))

    def member(self, cat: str, value: str, code: str, rel: str, line, path) -> None:
        """`value` が宣言の `cat` にあるか。 無ければ所見。"""
        if value in self.prof[cat]:
            self.used[cat].add(value)
        else:
            self.add(code, rel, line, path)

    # ---- 層 1 の照合

    def endpoint_ok(self, raw: str, prefix: "str | None" = None) -> bool:
        """IPv4 か host 名（`prefix` があれば網）が、 宣言の宛先か一般の値か。"""
        ip = _norm_ipv4(raw)
        if ip is None:
            h = _norm_host(raw)
            return h is not None and self._use_endpoint(h)
        if _generic_ip(ip):
            return True
        if prefix is not None and int(prefix) != 32:
            try:
                net = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
            except ValueError:
                return False
            hit = [n for n in self.prof.networks if net.subnet_of(n)]
        elif self._use_endpoint(ip):
            return True
        else:
            hit = [n for n in self.prof.networks if ipaddress.IPv4Address(ip) in n]
        self.used["endpoints.network"].update(str(n) for n in hit)
        return bool(hit)

    def _use_endpoint(self, h: str) -> bool:
        if h not in self._endpoints:
            return False
        for cat in ("equipment.cameras.ip", "endpoints"):
            if h in self.prof[cat]:
                self.used[cat].add(h)
        self.used["plc.endpoints"].update(
            e for e in self.prof["plc.endpoints"] if e.rsplit(":", 1)[0] == h)
        return True

    def name_ok(self, token: str) -> bool:
        """`trig_` で始まる語が、 宣言したトリガ・面か（`<名>__region...` の形も）。"""
        if token in CORE_TRIG_KEYS:
            return True
        for t in (token, token.split("__", 1)[0]):
            if t in self._names:
                for cat in ("triggers", "faces"):
                    self.used[cat].update(x for x in self.prof[cat] if t in (x, _base(x)))
                return True
        return False

    def generic(self, rel: str, kind: str, path: tuple, value, line,
                code_ip: str, code_name: str, sink: "list[Finding]") -> None:
        """決まった場所の外の値を照らす（補い）: IPv4 と `trig_` で始まる語。"""
        if not isinstance(value, str):
            return
        if kind == "value":
            for m in _IPV4.finditer(value):
                if not self.endpoint_ok(m.group(1), m.group(2)):
                    sink.append(Finding(code_ip, rel, line, path))
        for m in _TRIG.finditer(value):
            if not self.name_ok(m.group(0)):
                sink.append(Finding(code_name, rel, line, path))

    # ---- S1: config/ の下のファイル

    def s1(self, rel: str) -> "str | None":
        """許されないなら所見の符号。 許されるなら None。 **知らないものは黙って通さない。**"""
        name = rel.rsplit("/", 1)[-1]
        if os.path.islink(self.root / rel):
            return "S1-link"
        if name == ".gitkeep":
            return None if (self.root / rel).stat().st_size == 0 else "S1-file"
        if name.endswith(".md"):
            return None
        if rel in self.prof["config_files.extra"]:
            self.used["config_files.extra"].add(rel)
            return None
        if rel.startswith(IA_DIR) and "/" not in rel[len(IA_DIR):]:
            if name in IA_FIXED:
                return None
            if name.endswith(".json") and self._face_file_name(name[:-len(".json")]):
                return None
            return "S1-file"
        if rel.startswith(PDM_DIR):
            sub = rel[len(PDM_DIR):]
            if sub.startswith("defaults/"):
                return "S1-defaults"
            if any(part.startswith(".") for part in sub.split("/")):
                return "S1-file"
            return None if sub.endswith(PDM_SUFFIXES) else "S1-file"
        return "S1-file"

    def _face_file_name(self, stem: str) -> bool:
        """面ごとの設定のファイルの名前（`<面>` か `<面>__region...`）が宣言の面か。"""
        nm, sep, rest = stem.partition("__")
        if nm not in self._faces or (sep and not rest.startswith("region")):
            return False
        self.used["faces"].update(x for x in self.prof["faces"] if nm in (x, _base(x)))
        return True

    # ---- S2・S3（撮像側の案件の設定）

    def ia_config(self, rel: str, doc: JsonDoc, main: bool) -> None:
        d, prof = doc.data, self.prof
        if not isinstance(d, dict):
            raise Stop("config-field", "設定の全体が対応表ではありません", rel)

        # S2: 使うかどうか。 **書いていないものを推測しない**（主の設定は必ず書く）。
        for cap in ("plc", "nas", "ai"):
            sec = d.get(cap)
            if sec is None and not main:
                continue
            enabled = sec.get("enabled") if isinstance(sec, dict) else None
            if main and not isinstance(enabled, bool):
                raise Stop("config-field",
                           f"{cap}.enabled を true か false で書いてください（書いていないと、"
                           " 宣言と照らせません。 推測しません）", rel)
            line = doc.line("key", (cap,))
            if cap == "plc" and not prof.caps["plc"]:
                # **PLC を使わない案件に、 PLC の表を残さない**（起きたことの 2 つ目）。
                if not (isinstance(sec, dict) and list(sec) == ["enabled"]
                        and sec["enabled"] is False):
                    self.add("S2-plc", rel, line, ("plc",))
            elif cap == "plc":
                if main and enabled is not True:
                    self.add("S2-plc-enabled", rel, line, ("plc", "enabled"))
            elif isinstance(enabled, bool) and enabled is not prof.caps[cap]:
                self.add(f"S2-{cap}", rel, doc.line("value", (cap, "enabled")) or line,
                         (cap, "enabled"))

        # S3: 構成の名前と flow
        if "_profile" in d:
            v = d["_profile"]
            if isinstance(v, str) and v in prof["project.profiles"]:
                self.used["project.profiles"].add(v)
            else:
                self.add("S3-profile", rel, doc.line("value", ("_profile",)), ("_profile",))
        if (main or "flow" in d) and d.get("flow") != prof.flow:
            self.add("S3-flow", rel, doc.line("value", ("flow",))
                     or doc.line("key", ("flow",)), ("flow",))

        # 層 1: PLC の宛先（host と port の組で照らす）
        addr = _get(d, ("plc", "address"))
        if isinstance(addr, dict) and ("host" in addr or "port" in addr):
            host, port = addr.get("host"), addr.get("port")
            e = (_norm_endpoint(f"{host}:{port}") if isinstance(host, str)
                 and isinstance(port, int) and not isinstance(port, bool) else None)
            if e is not None and e in prof["plc.endpoints"]:
                self.used["plc.endpoints"].add(e)
            else:
                self.add("L1-plc-endpoint", rel, doc.line("key", ("plc", "address")),
                         ("plc", "address"))

    # ---- 層 1（鍵と値を 1 つずつ）

    def entries(self, rel: str, doc: JsonDoc) -> None:
        """決まった場所は決まった種類で照らし、 ほかは補いで照らす。"""
        ia = _is_ia_config(rel, doc.data)
        for kind, path, value, line in doc.entries:
            if ia and self._known(rel, doc, kind, path, value, line):
                continue
            self.generic(rel, kind, path, value, line, "L1-endpoint", "L1-name",
                         self.findings)

    def _known(self, rel: str, doc: JsonDoc, kind: str, path: tuple, value, line) -> bool:
        """決まった場所なら照らして True。 決まった場所でなければ False。"""
        n, head = len(path), (path[0] if path else None)
        # S3: カメラの id（camera と shot の鍵）、 面の名前（face_dict の鍵）
        if kind == "key" and n == 2 and head in ("camera", "shot"):
            self.member("equipment.cameras", path[1], "S3-camera", rel, line, path)
            return True
        if kind == "key" and n == 2 and head == "face_dict":
            self.member("faces", path[1], "S3-face", rel, line, path)
            return True
        if kind == "value" and path == ("_profile",):
            return True                                  # S3 で照らした
        # 層 1: カメラの宛先と製造番号
        if kind == "value" and n == 3 and head == "camera" and path[2] == "ip_address":
            if isinstance(value, str) and value and not self.endpoint_ok(value):
                self.add("L1-endpoint", rel, line, path)
            return True
        if kind == "value" and n == 3 and head == "camera" and path[2] == "serial_number":
            s = _scalar_text(value)
            if s is not None:
                self.member("equipment.cameras.serial", s, "L1-serial", rel, line, path)
            return True
        # 層 1: トリガ（`triggers` の一覧と、 PLC の `shot_request` の項目）
        if (kind == "value" and n >= 2 and path[-2] == "triggers"
                and isinstance(path[-1], int) and isinstance(value, str)):
            self.member("triggers", value, "L1-trigger", rel, line, path)
            return True
        if head != "plc":
            return False
        if (kind == "key" and n >= 3 and path[-2] == "shot_request"
                and _is_trigger_entry(_get(doc.data, path))):
            self.member("triggers", path[-1], "L1-trigger", rel, line, path)
            return True
        # 層 1: PLC の宛先（ia_config で照らした）と番地
        if kind == "value" and path[:2] == ("plc", "address") and n == 3:
            return True
        if kind == "value" and isinstance(path[-1], str) and path[-1] in ("data", "start", "end"):
            s = _scalar_text(value)
            if s is not None:
                self.member("plc.addresses", s, "L1-plc-address", rel, line, path)
            return True
        return False

    # ---- S4: deploy/ と versions.yaml が触れる設定のファイル

    def refs(self, rel: str, text: str) -> None:
        by_name: "dict[str, list[str]]" = {}
        for f in self.files:
            if f.startswith("config/"):
                by_name.setdefault(f.rsplit("/", 1)[-1], []).append(f)
        for i, ln in enumerate(text.splitlines(), 1):
            for m in _REF_PATH.finditer(ln):
                self.counts["refs"] += 1
                target = posixpath.normpath(m.group(0))
                if not target.startswith("config/"):
                    self.add("S4-not-allowed", rel, i)
                elif target not in self.files:
                    self.add("S4-missing", rel, i)
                elif target not in self.allowed:
                    self.add("S4-not-allowed", rel, i)
            for m in _REF_NAME.finditer(ln):
                self.counts["refs"] += 1
                found = by_name.get(m.group(0), [])
                if not found:
                    self.add("S4-missing", rel, i)
                elif not any(f in self.allowed for f in found):
                    self.add("S4-not-allowed", rel, i)

    def text_lines(self, rel: str, text: str, code_ip: str, code_name: str,
                   sink: "list[Finding]") -> None:
        for i, ln in enumerate(text.splitlines(), 1):
            self.generic(rel, "value", (), ln, i, code_ip, code_name, sink)

    # ---- 全体

    def run(self) -> None:
        root = self.root
        self.files = [f for f in list_files(root) if os.path.lexists(root / f)]
        main = root / IA_MAIN
        if IA_MAIN not in self.files or os.path.islink(main) or not main.is_file():
            raise Stop("config-missing",
                       f"{IA_MAIN} がありません（照らす相手が無いので、 合格にしません）")
        docs: "dict[str, JsonDoc]" = {}
        for rel in self.files:
            if not rel.startswith("config/"):
                continue
            code = self.s1(rel)
            if code is None:
                self.allowed.add(rel)
            else:
                self.add(code, rel)
            name = rel.rsplit("/", 1)[-1]
            if code == "S1-link" or not (root / rel).is_file() \
                    or name.endswith(".md") or name == ".gitkeep":
                continue
            self.counts["config"] += 1
            parts = rel.split("/")
            if rel.startswith(PDM_DIR) and len(parts) >= 4 and parts[2] in PDM_FACE_DIRS:
                # S3: 面ごとの設定のファイルの名前（置き場の中にフォルダは作らない）
                if len(parts) != 4 or not self._face_file_name(parts[3].rsplit(".", 1)[0]):
                    self.add("S3-face-file", rel)
            elif not (rel.startswith(IA_DIR) and code is None):
                # 層 1（補い）: ファイルの名前の中の `trig_` で始まる語
                for part in parts[1:]:
                    if any(not self.name_ok(m.group(0)) for m in _TRIG.finditer(part)):
                        self.add("L1-name", rel)
            if name.endswith(".json"):
                docs[rel] = JsonDoc(rel, _read(root, rel))
            elif name.endswith(".txt") or rel in self.prof["config_files.extra"]:
                text = _read(root, rel)
                if "\0" not in text:
                    self.text_lines(rel, text, "L1-endpoint", "L1-name", self.findings)
        for rel, doc in docs.items():
            if _is_ia_config(rel, doc.data):
                self.ia_config(rel, doc, rel == IA_MAIN)
            self.entries(rel, doc)
        for rel in self.files:
            if rel != "versions.yaml" and not (rel.startswith("deploy/")
                                               and not rel.endswith(".md")):
                continue
            p = root / rel
            if os.path.islink(p) or not p.is_file():
                continue
            text = _read(root, rel)
            if "\0" in text:
                continue
            self.refs(rel, text)
            if rel.startswith("deploy/"):
                # **参考だけ**（不合格にしない）。 強制するかは、 既存の案件の結果を見て決める。
                self.text_lines(rel, text, "deploy-endpoint", "deploy-name", self.notes)


# ---------------------------------------------------------------- 出力


def _field_text(field: "tuple | None") -> str:
    """欄の場所。 **決まった鍵だけを出す**（ほかの鍵は値かもしれないので `<*>`）。"""
    if not field:
        return "-"
    out = []
    for x in field:
        if isinstance(x, int):
            out.append(f"[{x}]")
        else:
            out.append(("." if out else "") + (x if x in SHOWN_KEYS else "<*>"))
    return "".join(out)


class Masks:
    """ファイルの道の中の、 決まっていない名前を伏せる（1 回の実行の中で同じ名前に同じ番号）。"""

    def __init__(self) -> None:
        self.n: "dict[str, int]" = {}

    def _mark(self, s: str) -> str:
        if s not in self.n:
            self.n[s] = len(self.n) + 1
        return f"<伏せ#{self.n[s]}>"

    def path(self, rel: str) -> str:
        parts = rel.split("/")
        out = []
        for i, part in enumerate(parts):
            if part in SHOWN_PARTS:
                out.append(part)
                continue
            stem, dot, ext = part.rpartition(".")
            if i == len(parts) - 1 and dot and stem and ("." + ext).lower() in SHOWN_SUFFIXES:
                out.append(self._mark(stem) + "." + ext)
            else:
                out.append(self._mark(part))
        return "/".join(out)


def _show(f: Finding, masks: Masks, label: str, what: dict) -> str:
    line = "-" if f.line is None else str(f.line)
    return (f"{label} [{f.code}] {what[f.code]}  file={masks.path(f.rel)}"
            f"  line={line}  field={_field_text(f.field)}")


def _unique(items: "list[Finding]") -> "list[Finding]":
    seen, out = set(), []
    for f in sorted(items, key=Finding.key):
        if f.key() not in seen:
            seen.add(f.key())
            out.append(f)
    return out


# ---------------------------------------------------------------- 宣言の増え方


def _git(root: Path, *args: str) -> "subprocess.CompletedProcess[bytes]":
    return subprocess.run(["git", *args], cwd=root, capture_output=True)


def expansion(root: Path, base: "str | None", head: Profile, yaml) -> "list[str]":
    """基準の commit の宣言と比べた、 種類ごとの増減の **数だけ**。

    **PR の中身を実行しない。** 読むのは git の中の基準の宣言だけ（`cat-file`）。
    取れなければ「比べていません」と言い、 これだけで不合格にはしない。
    """
    if base is None:
        return ["宣言の増減: 比べていません（--base-ref がありません）"]
    try:
        if _git(root, "cat-file", "-e", f"{base}^{{commit}}").returncode != 0:
            return ["宣言の増減: 比べていません（基準の commit が手元にありません）"]
        spec = f"{base}:./{PROFILE}"
        old = None
        if _git(root, "cat-file", "-e", spec).returncode == 0:
            size = _git(root, "cat-file", "-s", spec)
            if size.returncode != 0 or int(size.stdout.strip() or 0) > MAX_PROFILE_BYTES:
                return ["宣言の増減: 比べていません（基準の宣言を読めません）"]
            blob = _git(root, "cat-file", "blob", spec)
            if blob.returncode != 0:
                return ["宣言の増減: 比べていません（基準の宣言を読めません）"]
            try:
                old = parse_profile(blob.stdout.decode("utf-8-sig"), yaml)
            except (Stop, UnicodeDecodeError):
                return ["宣言の増減: 比べていません（基準の宣言が、 今の決まりでは読めません）"]
    except (OSError, ValueError):
        return ["宣言の増減: 比べていません（git を使えません）"]

    out = [f"宣言の増減（基準 {base[:12]} と比べた数。 値は出しません）:"]
    grew = old is None
    if old is None:
        out.append("  基準には宣言がありません（この変更で作られました）。"
                   " すべてを増えたものとして数えます")
    changed = False
    for cat in CATEGORIES:
        before = set() if old is None else old[cat]
        added, removed = len(head[cat] - before), len(before - head[cat])
        if added or removed:
            out.append(f"  {cat}: +{added} / -{removed}")
            changed, grew = True, grew or added > 0
    for k in ("plc", "nas", "ai"):
        b = None if old is None else old.caps[k]
        if b != head.caps[k]:
            out.append(f"  capabilities.{k}: {_tf(b)} → {_tf(head.caps[k])}")
            changed, grew = True, grew or head.caps[k] is True
    if old is not None and old.flow != head.flow:
        out.append("  project.flow: 変わりました（値は出しません）")
        changed = True
    if not changed:
        out.append("  変化なし")
    if grew:
        out.append("PROFILE EXPANSION DETECTED（宣言が増えています。 増えた値が本当にこの"
                   "案件のものかを、 レビューで確かめてください。 これだけでは不合格にしません）")
    return out


def _tf(v: "bool | None") -> str:
    return "（なし）" if v is None else ("true" if v else "false")


# ---------------------------------------------------------------- 入口

RESULT_ERROR = ("結果: ERROR（終了コード 2: 宣言・設定・検査の誤り。 照らせていないので"
                "合格にしません）")


class _Parser(argparse.ArgumentParser):
    """引数の誤りの文に、 渡された値を入れない。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        print("ERROR [usage] 引数が正しくありません（--help で使い方を見る）")
        print(RESULT_ERROR)
        self.exit(EXIT_ERROR)


def _load_profile(root: Path, yaml) -> Profile:
    p = root / PROFILE
    if os.path.islink(p):
        raise Stop("profile-missing", f"{PROFILE} がリンクです（読みません）")
    if not p.is_file():
        raise Stop("profile-missing",
                   f"{PROFILE} がありません。 この検査を有効にしたリポジトリでは、 根に"
                   "この案件の宣言が要ります（書き方は MirumeAI/.github の README）")
    if p.stat().st_size > MAX_PROFILE_BYTES:
        raise Stop("profile-yaml", f"{PROFILE} が大きすぎます（{MAX_PROFILE_BYTES // 1024} KB まで）")
    try:
        text = p.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Stop("profile-yaml", f"{PROFILE} が UTF-8 ではありません") from None
    return parse_profile(text, yaml)


def _summary(prof: Profile) -> str:
    s = prof.sets
    return ("宣言: カメラ {}・宛先 {}・網 {}・PLC の宛先 {}・番地 {}・トリガ {}・面 {}・"
            "追加のファイル {}（PLC={} NAS={} AI={}）").format(
        len(s["equipment.cameras"]), len(s["endpoints"]), len(s["endpoints.network"]),
        len(s["plc.endpoints"]), len(s["plc.addresses"]), len(s["triggers"]),
        len(s["faces"]), len(s["config_files.extra"]),
        _tf(prof.caps["plc"]), _tf(prof.caps["nas"]), _tf(prof.caps["ai"]))


def run(root: Path, base: "str | None", report_only: bool) -> int:
    masks = Masks()
    print(f"{PROFILE} と照らします（設定を読むだけ。 値は出しません）")
    try:
        if base is not None and not _HEX_REF.fullmatch(base):
            raise Stop("usage", "--base-ref は commit の 16 進（7〜40 字）だけを受け付けます")
        if not root.is_dir():
            raise Stop("usage", "--root がフォルダではありません")
        yaml = _yaml()
        prof = _load_profile(root, yaml)
        print(_summary(prof))
        ck = Checker(root, prof)
        ck.run()
    except Stop as e:
        where = "" if e.rel is None else f"  file={masks.path(e.rel)}"
        print(f"ERROR [{e.code}] {e.message}{where}")
        print(RESULT_ERROR)
        return EXIT_ERROR

    found, notes = _unique(ck.findings), _unique(ck.notes)
    print(f"検査: config/ の設定のファイル {ck.counts['config']}"
          f"・deploy/ と versions.yaml の中の設定のファイルへの参照 {ck.counts['refs']}")
    for f in found[:MAX_SHOWN]:
        print(_show(f, masks, "NG", WHAT))
    if len(found) > MAX_SHOWN:
        print(f"  ほか {len(found) - MAX_SHOWN} か所（出力を省きました）")
    if found:
        by: "dict[str, int]" = {}
        for f in found:
            by[f.code] = by.get(f.code, 0) + 1
        print("内訳: " + "・".join(f"{k} {v}" for k, v in sorted(by.items())))

    unused = [(cat, len(prof[cat] - ck.used[cat])) for cat in CATEGORIES
              if cat != "extensions" and prof[cat] - ck.used[cat]]
    if unused:
        print("注意: 宣言して使っていないもの（落としません。 要らなければ宣言から消す）: "
              + "・".join(f"{c} {n}" for c, n in unused))
    if notes:
        print(f"参考（強制しません。 deploy/ の中の値を照らした結果 {len(notes)} か所）:")
        for f in notes[:MAX_SHOWN]:
            print("  " + _show(f, masks, "参考", NOTE))

    for ln in expansion(root, base, prof, yaml):
        print(ln)

    if not found:
        print("結果: PASS（終了コード 0）")
        return EXIT_PASS
    print("  値は出していません。 手元でファイルの該当の行を開いて確かめ、 この案件の値で"
          "なければ消し、 この案件の値なら customer-profile.yaml に宣言してください。")
    if report_only:
        print(f"結果: REPORT ONLY（混入の疑い {len(found)} か所。 --report-only なので"
              "終了コード 0）")
        return EXIT_PASS
    print(f"結果: FAIL（終了コード 1: 別の案件の値・ファイル・構成が混ざっている疑い"
          f" {len(found)} か所）")
    return EXIT_VIOLATION


def main(argv: "list[str] | None" = None) -> int:
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", metavar="DIR",
                    help="顧客リポジトリ（既定は環境変数 CI_ROOT、 無ければ今のフォルダ）")
    ap.add_argument("--base-ref", metavar="SHA",
                    help="宣言の増減を比べる基準の commit（16 進）")
    ap.add_argument("--report-only", action="store_true",
                    help="混入の疑いを出しても終了コード 0 にする（誤りは 2 のまま）")
    a = ap.parse_args(argv)
    root = Path(a.root or os.environ.get("CI_ROOT") or ".")
    try:
        return run(root.resolve(), a.base_ref, a.report_only)
    except Exception:  # noqa: BLE001  予期しない誤りでも、 場所・中身・例外の文を出さない
        print("ERROR [internal] 予期しない誤りで止まりました（場所と中身は出しません）")
        print(RESULT_ERROR)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())

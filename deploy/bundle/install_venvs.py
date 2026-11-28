#!/usr/bin/env python3
"""据え付けた機体と顧客に見せる機体で、 配布物の束から `.venvs/web`・`.venvs/ai` を作る。

案件に依らない共通の道具（正本は MirumeAI/.github の同じ道。 案件の Repository には写しを置き、 写しの照らしが正本と
比べる。 MirumeAI/.github#237）。 両 Core と製品のパッケージを wheel にし、 index を使わず hash で入れる。 判定の venv には、
製品のパッケージを入れ、 製品の extra（部品の依存）が満たされることを確かめる。 決まりの正本は MirumeAI/development-docs の
`03_customer_development/customer_repository_structure.md`。
**開発の機体（`kind: dev`）では使わない**（開発の機体の venv は、 開発の道具で Core を作業ツリーから editable で入れる。
機体へ配る deploy/ は、 開発の道具と開発の機体だけの物の道を書かない〔混入の検査の S4〕）。

使い方（束の根で。 system の python3 で流す。 標準の部品だけを使う）:

    python3 deploy/bundle/install_venvs.py --machine machines/<記号>.yaml --system-release release/<名前>.yaml \\
        [--root <案件のフォルダ>] [--wheelhouse <束の置き場>] [--python python3.10] [--check] [--rebuild]

案件に依る所は、 この道具の木（据え付けた機体では一式の木。 一式の道具が SHA256SUMS で照らした物）の根の `pyproject.toml`
から読む（read_declaration）:
  [project].name                          製品のパッケージの配布物の名前。 System Release の product_package.name と同じで
                                          なければ止める。 `-` を `_` にした物が Python の名前（`python -m <Python の名前>
                                          doctor` で Core の版の範囲を照らす。 製品のパッケージに `__main__.py` が要る）
  [project.optional-dependencies]         名前の全部が、 判定の venv で依存が満たされることを照らす extra
  [project.entry-points."mirumeai_pdm.*"] PDM の差し込みの群ごとの、 製品のパッケージが登録する名前（無ければ照らさない）
  読み手は標準の部品だけの小さな TOML の読み手（read_toml。 機体の Python 3.10 には tomllib が無く、 system の python3 で
  ほかの部品を入れずに流すため）。 tomllib と同じ決まりで読み、 複数行の文字列などの読めない書き方と、 形の違う宣言は止める

読む物:
  versions.yaml（この Repository）      各 Core の節の tag・commit・sha256sums_sha256（束の SHA256SUMS の sha256）・
                                         wheel_sha256（本体の wheel の sha256）。 どれかが無ければ止める（値は Core の
                                         記録 releases/vX.Y.Z.json の同じ名前の鍵から写す）
  pyproject.toml（この Repository）      上の「案件に依る所」
  System Release の記録（--system-release） product_package の name・version・wheel_sha256
  機体の設定（--machine）                 machine.kind が installed か shown（dev は断る）
  束（--wheelhouse。 既定は <root>/wheelhouse）
    pdm/<PDM のタグ>/                     PDM の Product Release の束（SHA256SUMS・release.json・wheelhouse/）
    ia/<撮像側のタグ>/                    撮像側の束（同じ並びに、 根の requirements-web.lock・requirements-guard.lock）
    <製品の名前>/<製品の版>/              製品のパッケージの束（make_product_bundle.py が作る）
    camera/                               任意。人が手で置いたカメラの Python の部品の wheel（カメラの種類ごとに違うので、
                                           どの束にも載せない）。wheel だけを置く
                                           （リンク・フォルダ・ほかのファイルは断る）。無ければ今までどおりカメラの部品は入れない。
                                           束の配布物・venv の種（pip・setuptools）と同じ名前の wheel、`.pth`・`*.data/` を持つ
                                           wheel、形の崩れた道（`./`・`//`・末尾の `.` など）・sitecustomize・usercustomize・
                                           命令（console_scripts・gui_scripts）を持つ wheel、もう入った配布物のファイルを上書き
                                           するか、ほかの配布物の頭のフォルダに置く wheel は断る。これらは形の崩れとうっかりを
                                           止める照らしで、細工した wheel は止められない（中身は Web の処理で動く。置く人を信頼する）

入れる前の照らし（どれか 1 つでも外れたら、 何も作らずに止める）:
  - 各 Core の束の SHA256SUMS の sha256 が versions.yaml の sha256sums_sha256 と同じ。 製品の束は、 SHA256SUMS の sha256 が
    束の release.json の sha256sums_sha256 と同じ
  - SHA256SUMS に書いたファイルがすべてあり、 sha256 が同じ。 束の wheelhouse/ に、 SHA256SUMS に無いファイルが無い。
    書いた道は束の中だけ（絶対の道・`..`・リンクを断る）
  - 本体の wheel（release.json の wheel）の sha256 が、 versions.yaml の wheel_sha256（Core）か System Release の
    product_package.wheel_sha256（製品）と同じ。 Core の束の release.json の tag・commit が versions.yaml と同じ
  - 撮像側の lock の行が、 どれも `名前==版` と `--hash=sha256:` を持つ
  - System Release の product_package.version が出した版（数字を点でつないだ版と .postN。 開発の版・前出しの版でない）
  - System Release の product_package.name が、 根の pyproject.toml の [project].name と同じ（正規の形で比べる）
  - 撮像側の束が前提にした PDM の束（撮像側の release.json の pdm の tag・sha256sums_sha256。 組合せの正本の撮像側の lock
    から写した物）が、 versions.yaml の pdm と同じ
  - 門番の lock が、 PDM の束の wheel と同じ名前を違う版で固定していない（lock が PDM の束の版を入れ替えない）

カメラの部品（camera/ に wheel があるとき）: 名前・版・sha256 を画面に出し（配置の記録に写す）、 両方の venv の計画と印に
入れる（入れるのは Web の venv だけ。 起動の命令 serve が 2 つの印を比べるので、 印は両方に同じ物を書く。 置いた wheel が
替わると、 両方の venv の作り直しが要る）。 sha256 は道具がその場で計った値で、 wheel の出どころは照らさない（置く人が確かめ、
配置の計画に前もって書く）。

入れ方（pip は -I・--no-index・--require-hashes。 venv を作る命令も -I。 流す環境から PIP_・_PIP_・PYTHON で始まる名前を外し、
pip の設定のファイルを読まない）:
  .venvs/web   撮像側の requirements-web.lock → 撮像側の wheel（依存なし）→ 製品の wheel（依存なし）→ camera/ の wheel
               ぜんぶ（依存は、 もう入った物か camera/ の wheel だけで満たす。 満たせなければ入れずに止める）
  .venvs/ai    PDM の束の wheel ぜんぶ（SHA256SUMS から hash つきの要求を作る。 mirumeai-pdm[web] の依存の閉包）→
               撮像側の wheel（依存なし）→ 撮像側の requirements-guard.lock → 製品の wheel（依存なし）
入れた後の照らし: pip check・venv の配布物が束のまま（Web: Web の lock・撮像側・製品の wheel・camera/ の wheel。 判定: PDM の束の wheel
ぜんぶ・撮像側の wheel・門番の lock・製品の wheel。 名前と版がすべて同じで、 ほかの配布物は venv を作るときに入る pip と
setuptools だけ）・判定の venv で mirumeai-pdm の [web] と製品の extra（根の pyproject.toml の宣言の全部）の依存が満たされる・
製品のパッケージが PDM の差し込みの群に部品を登録している（根の pyproject.toml の宣言の群ごとに、 その名前だけ）・Core の版が
製品のパッケージが組める範囲に入る（各 venv の `python -m <Python の名前> doctor`。 判定の venv は `--judge-venv`。 範囲の表は
製品のパッケージだけが持つ）。

もうある venv: この道具の印（.mirume-installed-venv.json）があって計画と同じなら、 作らずに照らすだけ。 印が違うか途中で
止まった物は、 --rebuild のときだけ消して作り直す（印は venv を作った直後に「途中」で書き、 照らしが通ったら「完了」に
する）。 印の無い venv は消さない（止める）。

権限: 流す間、 umask にグループとほかの人の書き込みを外す 022 を足す（venv と pip
の子にも効く。 流す人がもっと絞った umask はそのまま）。 入れる前（--check でも）に、 根・.venvs・.venvs/web・.venvs/ai・
.cache（それぞれ、 在れば dir そのもの）と .cache/install_venvs（在れば中の全部）が、 流した利用者の持ち物で、 グループとほかの
人が書けないことを照らし、 外れたら何も作らずに止める（今ある物の権限は直さない。 書けた間に替えられたかもしれないため。
.venvs・.cache・.cache/install_venvs がリンクでも、 .cache/install_venvs の中にリンクがあっても止める）。 入れた後
（印が完了の venv を流し直すときも）に、 venv と
.cache/install_venvs の中を全部歩いて同じ照らしをし、 外れたら印を「途中」に戻して止める。 --check は venv の中を全部歩いて
照らし、 外れたら印を書かずに「合わない」と出す（1 で終わる）。 どちらも先に歩き、 外れたらその venv の python を流さない。
venv の中のリンクは持ち主だけを見て、 先は見ない（venv の python は system の物を指す）。 .cache/install_venvs の中の
リンクは断る（入れた後も）。 中身の書き換えは照らさない（照らすのは、
ほかの人が書けないこと）。
しないこと: index に行く・作業ルートやデータに触れる・常駐の組や unit を動かす・sudo・今ある物の権限を直す。 root では
流さない。

終了コード: 0 = 入れた（か、 --check で合う）・1 = 止めた（理由を出す）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MARK = ".mirume-installed-venv.json"
SCHEMA = 1
#: 判定の venv と Web の venv の Python（Core の配布物の wheel は cp310）
PYTHON = (3, 10)
CORES = {"pdm": "pdm", "image_acquisition_pdm": "ia"}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: 撮像側の束の根にある lock（版と hash の固定）。 Web の venv と判定の venv の門番の依存
IA_LOCKS = {"web": "requirements-web.lock", "guard": "requirements-guard.lock"}
#: 製品のパッケージが判定の venv で部品を登録する、 PDM の公開の面の「差し込みの群」の名前の頭。 群と名前は根の
#: pyproject.toml の [project.entry-points."mirumeai_pdm.*"] から読む（read_declaration）
PARTS_GROUP = "mirumeai_pdm."
#: 製品のパッケージの配布物の名前（根の pyproject.toml の [project].name）。 英字で始め、 英数字と _- だけ（`-` を `_` に
#: した物を Python の名前に使う。 雛形から案件を作る道具の決まりと同じ）
PRODUCT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
#: extra と差し込みの群に登録する名前
DECL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
#: 根の pyproject.toml の [tool.mirumeai] で読む鍵（ほかの鍵は書き違いとして止める）。 一式に足す根の物の並びは、 一式の
#: 道具が読む
TOOL_KEYS = ("install-set-extra",)
LOCK_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(\[[A-Za-z0-9,._-]+\])?==(?P<ver>[^\s;\\]+)"
                       r"(\s*;(?P<marker>[^\\]*?))?(\s+--hash=sha256:[0-9a-f]{64})+\s*$")
#: venv を作るときに Python（ensurepip）が入れる物。 束に無くても、 入れた後の照らしで「ほかの配布物」に数えない
VENV_SEED = ("pip", "setuptools")
#: 人が手で置いたカメラの Python の部品の wheel の置き場（束の置き場の下。 任意）
CAMERA_DIR = "camera"
CAMERA_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+!_]*")
#: 機体に入れてよい製品のパッケージの版（出した版。 数字を点でつないだ版〔X.Y.Z など〕と、 後ろの版 .postN。 開発の版・前出しの版は
#: 断る）
RELEASED_VERSION = re.compile(r"[0-9]+(\.[0-9]+)*(\.post[0-9]+)?")
#: 流す間の umask に足す物と、 道具が作る物・使う物で断る書き込み（グループとほかの人）
UMASK = 0o022
OPEN_WRITE = stat.S_IWGRP | stat.S_IWOTH


class Stop(Exception):
    """止める理由（人に見せる文）。"""


def say(text: str) -> None:
    print(f"[install] {text}", flush=True)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 小さな YAML の読み方（標準の部品だけ）

def _value(raw: str) -> "str | None":
    value = raw.split(" #", 1)[0].strip()
    if value.startswith(("'", '"')) and value.endswith(value[0]) and len(value) >= 2:
        value = value[1:-1]
    return None if value in ("", "null", "~") else value


PLAIN_KEY = re.compile(r"[a-z0-9_]+")
#: strict の読み方の値: 空・字のまま・同じ行で閉じた引用符のどれかで、 後ろに空白の後の注釈を受ける（注釈だけも受ける）
STRICT_VALUE = re.compile(r"""(?:(?:"[A-Za-z0-9_./+~-]*"|'[A-Za-z0-9_./+~-]*'|[A-Za-z0-9_./+~-]+)(?: +#.*)?|#.*)?""")


#: strict の読み方で断る字: 制御の字（タブ・改行・復帰のほか。 C1 の制御の字も）・YAML の読み手によって行の区切りになる字・BOM
STRICT_CHARS = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f\\u2028\\u2029\\ufeff]")


def strict_text(name: str, text: str) -> "list[str]":
    """strict の読み方の行。 断る字（STRICT_CHARS）があれば止める（Python の splitlines と YAML の読み手で行の区切りが
    分かれない・読み手が字を落とさないように）。"""
    bad = STRICT_CHARS.search(text)
    if bad:
        n = text.count("\n", 0, bad.start()) + 1
        raise Stop(f"{name} の {n} 行目: 使えない字（U+{ord(bad.group()):04X}。 制御の字・行の区切りの字・BOM）があります")
    return text.splitlines()


def strict_line(name: str, n: int, line: str, section: "str | None", heads: "tuple[str, ...]") -> None:
    """strict の読み方の 1 行（空の行と注釈の行は除く）が、 一番上の `鍵:`・2 つの空白で字下げした `鍵:` のどちらかで、 鍵は
    字のまま、 `:` の後は空か空白、 値は 1 行で閉じ（STRICT_VALUE）、 heads の節の見出しの行は値を持たないことを照らす。 ほかの
    書き方（引用符・`?`・タグ・アンカー・エスケープ・`:` の前の空白・ASCII でない空白・違う字下げ・流れの形・複数行の値）は、
    YAML では同じ鍵・別の深さ・文字列の中になりうるので止める（照らす値と YAML の読み手が読む値が食い違わないように）。
    開発の機体の venv を作る道具の同じ名前の関数と同じ形（変えるときは両方を変える。 その道具の試験が並べて照らす）。"""
    if not line.startswith(" "):
        body = line
    elif line.startswith("  ") and line[2:3] not in (" ", "\t") and section is not None:
        body = line[2:]
    else:
        raise Stop(f"{name} の {n} 行目: 字下げは 2 つの空白で書いてください（一番上の鍵の下の 1 段だけ）")
    key, sep, rest = body.partition(":")
    if not (sep and PLAIN_KEY.fullmatch(key)):
        raise Stop(f"{name} の {n} 行目: 鍵は字のまま（英小文字・数字・_ の後にすぐ `:`）で書いてください"
                   "（引用符・`?`・タグ・アンカー・エスケープ・`:` の前の空白を使わない）")
    value = rest.strip(" ")
    if (rest and not rest.startswith(" ")) or not STRICT_VALUE.fullmatch(value):
        raise Stop(f"{name} の {n} 行目: 値は `:` の後に空白を置き、 1 行で、 字のまま（英数字と _ . / + ~ -）か同じ行で閉じた"
                   "引用符で書いてください（流れの形・複数行・アンカー・タグを使わない）")
    if body is line and key in heads and value and not value.startswith("#"):
        raise Stop(f"{name} の {n} 行目: {key} の節の見出しの行には、 値を書かないでください（注釈だけ）")


def sections(path: Path, strict: "tuple[str, ...]" = ()) -> "dict[str, dict[str, str | None]]":
    """一番上の鍵ごとの、 1 段下の `鍵: 値`（もっと深い所と一覧は読まない。 同じ鍵が重なれば止める）。 strict に節の名前を
    渡すと、 ファイルのすべての行を strict_line で照らす（versions.yaml だけ）。"""
    out: "dict[str, dict[str, str | None]]" = {}
    section = None
    text = path.read_text(encoding="utf-8")
    for n, line in enumerate(strict_text(path.name, text) if strict else text.splitlines(), 1):
        if strict:
            if not line.strip(" ") or line.lstrip(" ").startswith("#"):
                continue
        elif not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent + 1]:
            raise Stop(f"{path.name} の {n} 行目: 字下げにタブがあります")
        if strict:
            strict_line(path.name, n, line, section, strict)
        key, sep, rest = line.strip().partition(":")
        if not sep:
            continue
        if indent == 0:
            section = key.strip()
            if section in out:
                raise Stop(f"{path.name} に {section} が 2 つあります")
            out[section] = {}
            continue
        if indent == 2 and section is not None:
            k = key.strip()
            if k in out[section]:
                raise Stop(f"{path.name} の {section}.{k} が 2 つあります")
            out[section][k] = _value(rest)
    return out


def read_versions(repo: Path) -> "dict[str, dict[str, str]]":
    """versions.yaml の各 Core の節（tag・commit・sha256sums_sha256・wheel_sha256）。 欠けたら止める。"""
    path = repo / "versions.yaml"
    if not path.is_file():
        raise Stop("versions.yaml がありません")
    data = sections(path, strict=tuple(CORES))
    out = {}
    for key in CORES:
        sec = data.get(key) or {}
        tag, commit, sums, wheel = (sec.get(k) for k in ("tag", "commit", "sha256sums_sha256", "wheel_sha256"))
        if not (tag and TAG.fullmatch(tag)):
            raise Stop(f"versions.yaml の {key}.tag が Product Release のタグ（vX.Y.Z）ではありません")
        if not (commit and HEX40.fullmatch(commit)):
            raise Stop(f"versions.yaml の {key}.commit が 40 桁の commit ではありません")
        for k, v in (("sha256sums_sha256", sums), ("wheel_sha256", wheel)):
            if not (v and HEX64.fullmatch(v)):
                raise Stop(f"versions.yaml の {key}.{k} がありません（64 桁の sha256。 束の hash を選んでから流す）")
        out[key] = {"tag": tag, "commit": commit, "sha256sums_sha256": sums, "wheel_sha256": wheel}
    return out


def read_machine(path: Path) -> "tuple[str, str]":
    """機体の設定の (記号, 種類)。 開発の機体は断る。"""
    if not path.is_file():
        raise Stop(f"機体の設定がありません: {path.name}")
    machine = sections(path).get("machine") or {}
    symbol, kind = machine.get("symbol"), machine.get("kind")
    if kind == "dev":
        raise Stop("開発の機体（kind: dev）では使いません。 開発の機体の venv は、 開発の道具で作ります（根の README の「開発」の節）")
    if kind not in ("installed", "shown"):
        raise Stop("機体の設定の machine.kind が installed か shown ではありません")
    if not symbol or symbol != path.stem:
        raise Stop("機体の設定の machine.symbol がファイルの名前（.yaml を除く）と違います")
    return symbol, kind


def read_product(path: Path) -> "dict[str, str]":
    """System Release の記録の product_package（name・version・wheel_sha256）。"""
    if not path.is_file():
        raise Stop(f"System Release の記録がありません: {path.name}")
    pp = sections(path).get("product_package") or {}
    name, version, wheel = pp.get("name"), pp.get("version"), pp.get("wheel_sha256")
    if not (name and NAME.fullmatch(name)):
        raise Stop("System Release の product_package.name がありません")
    if not (version and NAME.fullmatch(version)):
        raise Stop("System Release の product_package.version がありません")
    if not RELEASED_VERSION.fullmatch(version):
        # 開発の版・前出しの版（.devN・aN・bN・rcN など）は、据え付けた機体と顧客に見せる機体に入れない
        raise Stop(f"System Release の product_package.version が出した版（X.Y.Z）ではありません: {version}"
                   "（開発の版・前出しの版の製品のパッケージは機体に入れません。版を上げる Pull Request の後に束を作り直す）")
    if not (wheel and HEX64.fullmatch(wheel)):
        raise Stop("System Release の product_package.wheel_sha256 が 64 桁の sha256 ではありません")
    return {"name": name, "version": version, "wheel_sha256": wheel}



# ---------------------------------------------------------------- 小さな TOML の読み方（標準の部品だけ）
#
# 根の pyproject.toml を読む。 機体の Python 3.10 には tomllib が無く、 この道具は system の python3 でほかの部品を入れずに
# 流すので、 標準の部品だけで書く。 決まりは tomllib（TOML 1.0）と同じ形にし（同じ鍵・表を 2 度書く・点でつないだ鍵で
# 表を書き直す・行の中の表と並びに後から足すのを断る）、 読めない書き方は止める: 複数行の文字列（引用符 3 つ）は読まない。
# 日付と時刻と数は形だけを照らし、 値は読まない（読む鍵ではないため）。 試験が、 tomllib のある Python で同じに読むことを
# 照らす。

class TomlError(ValueError):
    """読めない・形が違う TOML（人に見せる文。 何行目かを添える）。"""


_TOML_BARE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
#: 文字列と注釈で断る字（タブのほかの制御の字。 tomllib と同じ）
_TOML_CTRL = frozenset(chr(i) for i in range(32) if i != 9) | {chr(127)}
_TOML_ESCAPES = {"b": "\b", "t": "\t", "n": "\n", "f": "\f", "r": "\r", '"': '"', "\\": "\\"}
_TOML_TIME = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]+)?"
#: 日付と時刻・時刻・数・真偽（tomllib と同じ順で照らす。 後ろの区切りは呼ぶ側が照らす）
_TOML_SCALAR = re.compile(
    "true|false"
    r"|[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    rf"(?:[Tt ]{_TOML_TIME}(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])?)?"
    rf"|{_TOML_TIME}"
    r"|0(?:x[0-9A-Fa-f](?:_?[0-9A-Fa-f])*|b[01](?:_?[01])*|o[0-7](?:_?[0-7])*)"
    r"|[+-]?(?:0|[1-9](?:_?[0-9])*)(?:\.[0-9](?:_?[0-9])*)?(?:[eE][+-]?[0-9](?:_?[0-9])*)?"
    r"|[+-]?(?:inf|nan)")
#: 値を読まない字のままの物（日付・時刻・数・真偽）の印
SCALAR = object()


class _Toml:
    """read_toml の中身。 表は dict、 並びは list、 文字列は str、 ほかの字のままの物は SCALAR。"""

    def __init__(self, text: str):
        if text.startswith("﻿"):
            raise TomlError("1 行目: BOM があります")
        self.s, self.i = text.replace("\r\n", "\n"), 0
        self.data: dict = {}
        self.explicit: "set[tuple]" = set()      # 見出しか、 点でつないだ鍵で書いた表（もう見出しで書けない）
        self.frozen: "set[tuple]" = set()        # 行の中の表と並び（中にも、 後から足せない）
        self.pending: "list[tuple]" = []         # 今の節で点でつないだ鍵が書いた表（次の見出しで explicit にする）

    def fail(self, msg: str) -> TomlError:
        return TomlError(f"{self.s.count(chr(10), 0, self.i) + 1} 行目: {msg}")

    def peek(self, n: int = 1) -> str:
        return self.s[self.i:self.i + n]

    def skip_ws(self) -> None:
        while self.peek() in ("\t", " ") and self.peek():
            self.i += 1

    def skip_comment(self) -> None:
        if self.peek() == "#":
            end = self.s.find("\n", self.i)
            end = len(self.s) if end < 0 else end
            for j in range(self.i, end):
                if self.s[j] in _TOML_CTRL:
                    self.i = j
                    raise self.fail("注釈に使えない字があります")
            self.i = end

    def is_frozen(self, key: tuple) -> bool:
        return any(key[:n] in self.frozen for n in range(1, len(key) + 1))

    def nest(self, key: tuple, lists: bool = True, root: "dict | None" = None) -> dict:
        """key の表（無ければ作る）。 lists なら、 表の並びは最後の表をたどる。 表でない物にあたれば止める。"""
        cont = self.data if root is None else root
        for k in key:
            if k not in cont:
                cont[k] = {}
            cont = cont[k]
            if lists and isinstance(cont, list) and cont:
                cont = cont[-1]
            if not isinstance(cont, dict):
                raise self.fail(f"{'.'.join(key)} は表ではありません")
        return cont

    def parse(self) -> dict:
        header: tuple = ()
        while True:
            self.skip_ws()
            c = self.peek()
            if c == "":
                return self.data
            if c == "\n":
                self.i += 1
                continue
            if c == "[":
                self.explicit.update(self.pending)
                self.pending.clear()
                header = self.array_table() if self.peek(2) == "[[" else self.table()
                self.skip_ws()
            elif c in _TOML_BARE or c in "\"'":
                self.key_value(header)
                self.skip_ws()
            elif c != "#":
                raise self.fail("鍵・表の見出し・注釈のどれでもない行です")
            self.skip_comment()
            if self.peek() not in ("", "\n"):
                raise self.fail("1 つの行に 2 つ以上を書いています")

    def table(self) -> tuple:
        self.i += 1
        self.skip_ws()
        key = self.key()
        if self.peek() != "]":
            raise self.fail("表の見出しが ] で閉じていません")
        self.i += 1
        if key in self.explicit or self.is_frozen(key):
            raise self.fail(f"表 {'.'.join(key)} を 2 度書いています")
        self.explicit.add(key)
        self.nest(key)
        return key

    def array_table(self) -> tuple:
        self.i += 2
        self.skip_ws()
        key = self.key()
        if self.peek(2) != "]]":
            raise self.fail("表の並びの見出しが ]] で閉じていません")
        self.i += 2
        if self.is_frozen(key):
            raise self.fail(f"{'.'.join(key)} には後から足せません")
        # 新しい表の中は、 前の表で書いた表と行の中の物を書けるようにする（tomllib と同じ）
        self.explicit = {k for k in self.explicit if k[:len(key)] != key}
        self.frozen = {k for k in self.frozen if k[:len(key)] != key}
        self.explicit.add(key)
        cont = self.nest(key[:-1])
        if key[-1] in cont:
            if not isinstance(cont[key[-1]], list):
                raise self.fail(f"{'.'.join(key)} は表の並びではありません")
            cont[key[-1]].append({})
        else:
            cont[key[-1]] = [{}]
        return key

    def key_value(self, header: tuple) -> None:
        key, value = self.pair()
        for n in range(1, len(key)):
            if header + key[:n] in self.explicit:
                raise self.fail(f"表 {'.'.join(header + key[:n])} を点でつないだ鍵で書き直しています")
            self.pending.append(header + key[:n])
        if self.is_frozen(header + key[:-1]):
            raise self.fail(f"{'.'.join(header + key[:-1])} には後から足せません")
        nest = self.nest(header + key[:-1])
        if key[-1] in nest:
            raise self.fail(f"{'.'.join(header + key)} を 2 度書いています")
        if isinstance(value, (dict, list)):
            self.frozen.add(header + key)
        nest[key[-1]] = value

    def pair(self) -> "tuple[tuple, object]":
        key = self.key()
        if self.peek() != "=":
            raise self.fail("鍵の後に = がありません")
        self.i += 1
        self.skip_ws()
        return key, self.value()

    def key(self) -> tuple:
        parts = [self.key_part()]
        self.skip_ws()
        while self.peek() == ".":
            self.i += 1
            self.skip_ws()
            parts.append(self.key_part())
            self.skip_ws()
        return tuple(parts)

    def key_part(self) -> str:
        c = self.peek()
        if c and c in _TOML_BARE:
            start = self.i
            while self.peek() and self.peek() in _TOML_BARE:
                self.i += 1
            return self.s[start:self.i]
        if c in ("\"", "'") and c:
            return self.string()
        raise self.fail("鍵の書き方が違います")

    def string(self) -> str:
        quote = self.peek()
        if self.peek(3) == quote * 3:
            raise self.fail("複数行の文字列は読めません（1 行の文字列で書く）")
        self.i += 1
        out = []
        while True:
            c = self.peek()
            if c == "":
                raise self.fail("文字列が閉じていません")
            if c == quote:
                self.i += 1
                return "".join(out)
            if c in _TOML_CTRL:
                raise self.fail("文字列に使えない字があります")
            if c == "\\" and quote == "\"":
                e = self.s[self.i + 1:self.i + 2]
                if e in ("u", "U"):
                    width = 4 if e == "u" else 8
                    digits = self.s[self.i + 2:self.i + 2 + width]
                    if len(digits) != width or any(d not in "0123456789abcdefABCDEF" for d in digits):
                        raise self.fail("文字列の \\u・\\U の後が 16 進ではありません")
                    code = int(digits, 16)
                    if not (code <= 0xD7FF or 0xE000 <= code <= 0x10FFFF):
                        raise self.fail("文字列の \\u・\\U が字ではありません")
                    out.append(chr(code))
                    self.i += 2 + width
                    continue
                if e not in _TOML_ESCAPES:
                    raise self.fail("文字列に読めない \\ があります")
                out.append(_TOML_ESCAPES[e])
                self.i += 2
                continue
            out.append(c)
            self.i += 1

    def value(self) -> object:
        c = self.peek()
        if c in ("\"", "'") and c:
            return self.string()
        if c == "[":
            return self.array()
        if c == "{":
            return self.inline_table()
        m = _TOML_SCALAR.match(self.s, self.i)
        if not m or not m.group():
            raise self.fail("値の書き方が違います")
        self.i = m.end()
        return SCALAR

    def skip_array_space(self) -> None:
        while True:
            start = self.i
            while self.peek() and self.peek() in " \t\n":
                self.i += 1
            self.skip_comment()
            if self.i == start:
                return

    def array(self) -> list:
        self.i += 1
        out: list = []
        self.skip_array_space()
        if self.peek() == "]":
            self.i += 1
            return out
        while True:
            out.append(self.value())
            self.skip_array_space()
            c = self.peek()
            if c == "]":
                self.i += 1
                return out
            if c != ",":
                raise self.fail("並びが閉じていません（, か ] が要る）")
            self.i += 1
            self.skip_array_space()
            if self.peek() == "]":
                self.i += 1
                return out

    def inline_table(self) -> dict:
        self.i += 1
        out: dict = {}
        frozen: "set[tuple]" = set()
        self.skip_ws()
        if self.peek() == "}":
            self.i += 1
            return out
        while True:
            key, value = self.pair()
            if any(key[:n] in frozen for n in range(1, len(key) + 1)):
                raise self.fail(f"行の中の表の {'.'.join(key)} には後から足せません")
            nest = self.nest(key[:-1], lists=False, root=out)
            if key[-1] in nest:
                raise self.fail(f"行の中の表に {'.'.join(key)} が 2 度あります")
            nest[key[-1]] = value
            self.skip_ws()
            c = self.peek()
            if c == "}":
                self.i += 1
                return out
            if c != ",":
                raise self.fail("行の中の表が閉じていません（, か } が要る。 行をまたがない）")
            if isinstance(value, (dict, list)):
                frozen.add(key)
            self.i += 1
            self.skip_ws()


def read_toml(text: str) -> dict:
    """TOML の文を読む（tomllib と同じ決まりの、 この読み手が読める書き方だけ。 読めなければ TomlError）。"""
    return _Toml(text).parse()


def read_declaration(repo: Path) -> dict:
    """根の pyproject.toml の、 案件に依る所（製品の名前・Python の名前・extra・部品の登録・一式に足す根の物）。 形が違えば
    止める。 読むのは repo の根の物だけ（据え付けた機体では、 一式の道具が照らした一式の木）。"""
    path = repo / "pyproject.toml"
    if path.is_symlink() or not path.is_file():
        raise Stop("根に pyproject.toml がありません（製品のパッケージの宣言を読む）")
    try:
        data = read_toml(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        raise Stop("pyproject.toml を UTF-8 で読めません") from None
    except TomlError as e:
        raise Stop(f"pyproject.toml を読めません（{e}）") from None
    project = data.get("project")
    if not isinstance(project, dict):
        raise Stop("pyproject.toml に [project] がありません")
    name = project.get("name")
    if not isinstance(name, str) or not PRODUCT_NAME.fullmatch(name):
        raise Stop("pyproject.toml の [project].name に、使えない字があります（英字で始まり、英数字と _- だけ）")
    extras = project.get("optional-dependencies", {})
    if not isinstance(extras, dict) or any(
            not DECL_NAME.fullmatch(k) or not isinstance(v, list) or not all(isinstance(x, str) for x in v)
            for k, v in extras.items()):
        raise Stop("pyproject.toml の [project.optional-dependencies] の形が違います（名前ごとに、要求の文字列の並び）")
    groups = project.get("entry-points", {})
    if not isinstance(groups, dict):
        raise Stop("pyproject.toml の [project.entry-points] の形が違います")
    parts: "dict[str, list[str]]" = {}
    for group, table in groups.items():
        if not group.startswith(PARTS_GROUP):
            continue
        if not DECL_NAME.fullmatch(group[len(PARTS_GROUP):]) or not isinstance(table, dict) or not table or any(
                not DECL_NAME.fullmatch(k) or not isinstance(v, str) for k, v in table.items()):
            raise Stop(f"pyproject.toml の [project.entry-points.\"{group}\"] の形が違います（名前ごとに、入口の文字列）")
        parts[group] = sorted(table)
    tool = data.get("tool", {})
    ours = tool.get("mirumeai", {}) if isinstance(tool, dict) else None
    if not isinstance(ours, dict):
        raise Stop("pyproject.toml の [tool.mirumeai] の形が違います")
    unknown = sorted(set(ours) - set(TOOL_KEYS))
    if unknown:
        raise Stop(f"pyproject.toml の [tool.mirumeai] に、読まない鍵があります: {', '.join(unknown)}"
                   f"（読む鍵は {', '.join(TOOL_KEYS)}。書き違いを確かめる）")
    extra = ours.get("install-set-extra", [])
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        raise Stop("pyproject.toml の [tool.mirumeai].install-set-extra は、根の物の名前（文字列）の並びにします")
    return {"name": name, "package": name.replace("-", "_"), "extras": sorted(extras), "parts": parts,
            "install_set_extra": list(extra)}


# ---------------------------------------------------------------- 束

def _inside(path: Path, top: Path) -> bool:
    try:
        path.relative_to(top)
        return True
    except ValueError:
        return False


def read_sums(bundle: Path) -> "dict[str, str]":
    """束の SHA256SUMS（道 → sha256）。 束の外を指す道・重なり・形の違う行を断る。"""
    sums_path = bundle / "SHA256SUMS"
    if not sums_path.is_file() or sums_path.is_symlink():
        raise Stop(f"束に SHA256SUMS がありません: {bundle.name}")
    out = {}
    for n, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
        if not m:
            raise Stop(f"{bundle.name} の SHA256SUMS の {n} 行目の形が違います")
        rel = m.group(2)
        if rel.startswith("/") or ".." in Path(rel).parts or rel in out:
            raise Stop(f"{bundle.name} の SHA256SUMS の {n} 行目の道が束の外か重なっています")
        out[rel] = m.group(1)
    return out


def check_bundle(bundle: Path, *, sha256sums_sha256: "str | None", wheel_sha256: str, label: str,
                 tag: "str | None" = None, commit: "str | None" = None, product: "dict | None" = None) -> dict:
    """束を照らし、 release.json の中身を返す。 sha256sums_sha256 が None のとき（製品）は release.json と照らす。"""
    if not bundle.is_dir():
        raise Stop(f"{label} の束がありません（置き場を確かめる）")
    real = bundle.resolve()
    sums_file = sha256_file(bundle / "SHA256SUMS") if (bundle / "SHA256SUMS").is_file() else ""
    sums = read_sums(bundle)
    release_path = bundle / "release.json"
    if not release_path.is_file() or release_path.is_symlink():
        raise Stop(f"{label} の束に release.json がありません")
    try:
        release = json.loads(release_path.read_text(encoding="utf-8"))
    except ValueError:
        raise Stop(f"{label} の束の release.json を読めません") from None
    if not isinstance(release, dict):
        raise Stop(f"{label} の束の release.json の形が違います")
    if sha256sums_sha256 is not None and sums_file != sha256sums_sha256:
        raise Stop(f"{label} の束の SHA256SUMS が versions.yaml の sha256sums_sha256 と違います")
    if sha256sums_sha256 is None and sums_file != release.get("sha256sums_sha256"):
        raise Stop(f"{label} の束の SHA256SUMS が release.json の sha256sums_sha256 と違います")
    for rel, digest in sums.items():
        path = bundle / rel
        if path.is_symlink() or not path.is_file() or not _inside(path.resolve(), real):
            raise Stop(f"{label} の束に、SHA256SUMS に書いたファイルが無いか、リンクです: {rel}")
        if sha256_file(path) != digest:
            raise Stop(f"{label} の束のファイルが SHA256SUMS と違います: {rel}")
    wheelhouse = bundle / "wheelhouse"
    if not wheelhouse.is_dir():
        raise Stop(f"{label} の束に wheelhouse/ がありません")
    for path in sorted(wheelhouse.rglob("*")):
        rel = path.relative_to(bundle).as_posix()
        if path.is_symlink() or (path.is_file() and rel not in sums):
            raise Stop(f"{label} の束の wheelhouse/ に、SHA256SUMS に無いファイルかリンクがあります: {rel}")
    wheel = release.get("wheel")
    if not isinstance(wheel, str) or "/" in wheel or not wheel.endswith(".whl"):
        raise Stop(f"{label} の束の release.json に本体の wheel がありません")
    if sums.get(f"wheelhouse/{wheel}") != wheel_sha256 or release.get("wheel_sha256") != wheel_sha256:
        raise Stop(f"{label} の本体の wheel の sha256 が、選んだ値（versions.yaml か System Release）と違います")
    if tag is not None and (release.get("tag") != tag or release.get("commit") != commit):
        raise Stop(f"{label} の束の release.json の tag・commit が versions.yaml と違います")
    if product is not None:
        if normalize(str(release.get("product", ""))) != normalize(product["name"]) \
                or str(release.get("version")) != product["version"]:
            raise Stop(f"{label} の束の release.json の名前・版が System Release と違います")
    return release


def check_lock(bundle: Path, name: str, sums: "dict[str, str]") -> "tuple[Path, list[list]]":
    """撮像側の束の lock の道と、 固定した物の並び（[名前, 版, 環境の印か None]）。 SHA256SUMS に載っていて、 どの要求も
    `名前==版` と hash を持つ（行末の `\\` で続く行はつなぐ）。"""
    if name not in sums:
        raise Stop(f"撮像側の束に {name} がありません（SHA256SUMS に載った lock が要る）")
    path = bundle / name
    pins: "list[list]" = []
    logical, start = "", 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not logical:
            start = n
            if line.lstrip().startswith("#"):
                continue
        text = re.sub(r"\s+#.*$", "", line).rstrip()
        if text.endswith("\\"):
            logical += text[:-1] + " "
            continue
        logical = (logical + text).strip()
        if logical:
            m = LOCK_LINE.match(logical)
            if not m:
                raise Stop(f"撮像側の {name} の {start} 行目が、`名前==版` と `--hash=sha256:` の形ではありません")
            pins.append([normalize(m.group("name")), m.group("ver"), (m.group("marker") or "").strip() or None])
        logical = ""
    if logical:
        raise Stop(f"撮像側の {name} の終わりが `\\` で切れています")
    return path, pins


def wheel_requirements(bundle: Path, sums: "dict[str, str]", only: "str | None" = None) -> str:
    """束の wheel から、 hash つきの要求（`名前==版 --hash=sha256:…`）を作る。 only を渡すと、 その wheel だけ。"""
    lines = []
    for rel, digest in sorted(sums.items()):
        if not rel.startswith("wheelhouse/") or not rel.endswith(".whl"):
            continue
        filename = rel.split("/", 1)[1]
        if only is not None and filename != only:
            continue
        parts = filename[:-4].split("-")
        if len(parts) < 5:
            raise Stop(f"{bundle.name} の wheel の名前の形が違います: {filename}")
        lines.append(f"{normalize(parts[0])}=={parts[1]} --hash=sha256:{digest}")
    if only is not None and len(lines) != 1:
        raise Stop(f"{bundle.name} に本体の wheel がありません: {only}")
    return "\n".join(lines) + "\n"


def read_camera(wheelhouse: Path) -> "list[dict]":
    """人が `<束の置き場>/camera/` に手で置いたカメラの部品の wheel（名前・版・sha256・ファイルの名前）。 置き場が無ければ空。
    置き場がリンク・wheel でない物・リンク・名前の形が違う・同じ名前が 2 つなら止める。"""
    d = wheelhouse / CAMERA_DIR
    if not d.exists() and not d.is_symlink():
        return []
    if d.is_symlink() or not d.is_dir():
        raise Stop(f"束の置き場の {CAMERA_DIR}/ はフォルダにします（リンクは断る）")
    out: "list[dict]" = []
    for f in sorted(d.iterdir()):
        if f.is_symlink() or not f.is_file() or not f.name.endswith(".whl"):
            raise Stop(f"{CAMERA_DIR}/ に wheel でない物かリンクがあります: {f.name}（カメラの部品の wheel だけを置く）")
        parts = f.name[:-4].split("-")
        if len(parts) not in (5, 6) or not NAME.fullmatch(parts[0]) or not CAMERA_VERSION.fullmatch(parts[1]):
            raise Stop(f"{CAMERA_DIR}/ の wheel の名前の形が違います: {f.name}")
        name = normalize(parts[0])
        if any(c["name"] == name for c in out):
            raise Stop(f"{CAMERA_DIR}/ に {name} の wheel が 2 つあります")
        camera_wheel_files(f)
        out.append({"name": name, "version": parts[1], "sha256": sha256_file(f), "file": f.name})
    return out


def has_scripts(entry_points: str) -> bool:
    """entry_points.txt が console_scripts か gui_scripts の節に 1 つでも命令を持つか（注釈と空の行は数えない）。"""
    section = None
    for line in entry_points.splitlines():
        t = line.strip()
        if not t or t[0] in "#;":
            continue
        if t.startswith("[") and t.endswith("]"):
            section = t[1:-1].strip()
        elif section in ("console_scripts", "gui_scripts"):
            return True
    return False


def camera_wheel_files(wheel: Path) -> "list[str]":
    """カメラの部品の wheel が site-packages に置くファイルの道（dist-info の中を除く）。 次の wheel は断る。
    外を指す道・形の崩れた道（pip は道を直して置くので、書かれた文字と置く道が違う形は照らしを外せる）・Python が起動の
    たびに読む `.pth` と sitecustomize・usercustomize・site-packages の外へ置く `*.data/`・命令（console_scripts・
    gui_scripts。venv の bin/ の物を黙って上書きしうる）。 形の崩れとうっかりを止める照らしで、細工した wheel は止められない。"""
    try:
        with zipfile.ZipFile(wheel) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            entry_points = [n for n in names if n.count("/") == 1 and n.split("/")[0].endswith(".dist-info")
                            and n.endswith("/entry_points.txt")]
            scripts = [z.read(n).decode("utf-8", "replace") for n in entry_points]
    except (OSError, zipfile.BadZipFile):
        raise Stop(f"{CAMERA_DIR}/ の {wheel.name} を wheel として読めません") from None
    for text in scripts:
        if has_scripts(text):
            raise Stop(f"{CAMERA_DIR}/ の {wheel.name} は命令（console_scripts・gui_scripts）を持ちます（venv の bin/ の物を"
                       "上書きしうるので入れない）")
    out = []
    for n in names:
        if n.startswith("/") or ".." in n.split("/") or "\\" in n:
            raise Stop(f"{CAMERA_DIR}/ の {wheel.name} に、外を指す道があります")
        if posixpath.normpath(n) != n:
            raise Stop(f"{CAMERA_DIR}/ の {wheel.name} に、形の崩れた道（./・//・末尾の . など）があります")
        top = n.split("/", 1)[0]
        stem = top.split(".", 1)[0]
        if n.endswith(".pth") or top.endswith(".data") or stem in ("sitecustomize", "usercustomize"):
            raise Stop(f"{CAMERA_DIR}/ の {wheel.name} は、.pth・sitecustomize・usercustomize か .data/ を持ちます"
                       "（起動のたびに流れる物・site-packages の外へ置く物は入れない）")
        if not top.endswith(".dist-info"):
            out.append(n)
    return out


def wheel_pins(sums: "dict[str, str]") -> "list[list]":
    """束の wheel ぜんぶの [名前, 版, None]（wheel の名前から読む）。"""
    out = []
    for rel in sorted(sums):
        if rel.startswith("wheelhouse/") and rel.endswith(".whl"):
            parts = rel.split("/", 1)[1][:-4].split("-")
            out.append([normalize(parts[0]), parts[1], None])
    return out


def merge_pins(label: str, *groups: "list[list]") -> "list[list]":
    """固定した物の並びを 1 つにまとめる。 同じ名前を違う版（か違う環境の印）で固定していたら止める。"""
    seen: "dict[str, list]" = {}
    for group in groups:
        for name, version, marker in group:
            prev = seen.get(name)
            if prev is not None and (prev[1], prev[2]) != (version, marker):
                raise Stop(f"{label} に入れる {name} の版が、束の間で違います（{prev[1]} と {version}。 束の組合せを確かめる）")
            seen[name] = [name, version, marker]
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------- 流す

def pip_env() -> "dict[str, str]":
    """pip に渡す環境（PIP_・_PIP_・PYTHON で始まる名前を外し、 設定のファイルを読まない。 index の道を環境から入れさせず、
    PYTHONPATH の上の物を「入っている」と見なさせない）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "_PIP_", "PYTHON"))}
    env.update(PIP_CONFIG_FILE=os.devnull, PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return env


class Runner:
    def __init__(self, log: "Path | None"):
        self.log = log

    def __call__(self, cmd: "list[str]", capture: bool = False, env: "dict | None" = None) -> str:
        if self.log is not None:
            with self.log.open("a", encoding="utf-8") as f:
                f.write("\n$ " + " ".join(cmd) + "\n")
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if self.log is not None:
            with self.log.open("a", encoding="utf-8") as f:
                f.write(r.stdout + r.stderr)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout).strip().splitlines()[-3:]
            raise Stop(f"落ちました: {' '.join(cmd[:5])} …（{' / '.join(tail)}）")
        return r.stdout if capture else ""


def pip_install(run, py: Path, req: Path, find_links: "list[Path]", no_deps: bool = False) -> None:
    cmd = [str(py), "-I", "-m", "pip", "install", "--no-index", "--require-hashes", "--only-binary", ":all:"]
    for d in find_links:
        cmd += ["--find-links", str(d)]
    if no_deps:
        cmd.append("--no-deps")
    run(cmd + ["-r", str(req)], env=pip_env())


#: venv に入った配布物のファイルの道（site-packages からの道。 RECORD に載った物）。 カメラの部品が上書きしないかを照らす
_FILES = r"""import importlib.metadata as m, json
print(json.dumps(sorted({str(f).replace("\\", "/") for d in m.distributions() for f in (d.files or [])})))
"""


#: 入れた後の照らし（venv の中で流す。 標準の部品と pip が持つ packaging だけ）。 JSON で合わない所の文の並びを出す
_VERIFY = r"""import importlib.metadata as m, json, re, sys
try:
    from packaging.markers import Marker
    from packaging.requirements import Requirement
    from packaging.version import Version
except ImportError:
    from pip._vendor.packaging.markers import Marker
    from pip._vendor.packaging.requirements import Requirement
    from pip._vendor.packaging.version import Version
want = json.loads(sys.argv[1])
norm = lambda s: re.sub(r"[-_.]+", "-", s).lower()
def same(a, b):
    try:
        return Version(a) == Version(b)
    except Exception:
        return a == b
bad = []
have = {}
for d in m.distributions():
    have.setdefault(norm(d.metadata["Name"] or ""), []).append(d)
for name, ds in sorted(have.items()):
    if len(ds) > 1:
        bad.append(name + " が 2 つ以上入っています")
have = {k: v[0] for k, v in have.items()}
pinned = set()
for name, version, marker in want["pins"]:
    if marker is not None and not Marker(marker).evaluate():
        continue
    pinned.add(norm(name))
    d = have.get(norm(name))
    if d is None:
        bad.append(name + " が入っていません")
    elif not same(d.version, version):
        bad.append(name + " の版が " + version + " ではありません（" + d.version + "）")
for name in sorted(set(have) - pinned - set(want["seed"])):
    bad.append(name + " は束に無い配布物です")
for name, extra in want["extras"]:
    for text in m.requires(name) or []:
        req = Requirement(text)
        if req.marker is not None and not req.marker.evaluate({"extra": extra}):
            continue
        d = have.get(norm(req.name))
        if d is None:
            bad.append(name + "[" + extra + "] の依存 " + req.name + " がありません")
        elif not req.specifier.contains(d.version, prereleases=True):
            bad.append(name + "[" + extra + "] の依存 " + req.name + " の版が合いません")
if want.get("parts"):
    dist, groups = want["parts"]
    d = have.get(norm(dist))
    for group, names in sorted(groups.items()):
        got = sorted(e.name for e in (d.entry_points if d else []) if e.group == group)
        if got != sorted(names):
            bad.append(dist + " が " + group + " に " + "・".join(sorted(names)) + " だけを 1 つずつ登録していません")
print(json.dumps(bad, ensure_ascii=False))
"""


def verify(run, py: Path, want: dict) -> "list[str]":
    bad = []
    try:
        run([str(py), "-I", "-m", "pip", "check"], env=pip_env())
    except Stop as e:
        bad.append(f"依存が足りないか合いません（pip check）: {e}")
    out = run([str(py), "-I", "-c", _VERIFY, json.dumps(want)], capture=True)
    try:
        bad += [str(x) for x in json.loads(out or "null")]
    except (ValueError, TypeError):
        bad.append("入れた後の照らしの結果を読めません")
    # Core の版が、製品のパッケージが組める範囲に入るかを、その venv の製品のパッケージの命令 `doctor` で照らす（判定の
    # venv は PDM も要る）。 範囲の表は製品のパッケージだけが持つ（写さない）。 Python の名前は根の pyproject.toml の
    # [project].name から作った物（read_declaration）
    package = want["package"]
    try:
        run([str(py), "-I", "-m", package, "doctor"] + (["--judge-venv"] if want.get("judge") else []),
            env=pip_env())
    except Stop as e:
        bad.append(f"Core の版が、製品のパッケージが組める範囲に入りません（python -m {package} doctor）: {e}")
    return bad


# ---------------------------------------------------------------- venv の状態

def venv_state(venv: Path, want: dict) -> str:
    """none（無い）・same（印が計画と同じで完了）・other（印が違う）・partial（途中）・foreign（印が無い）。"""
    if not venv.exists() and not venv.is_symlink():
        return "none"
    mark = venv / MARK
    if venv.is_symlink() or not mark.is_file():
        return "foreign"
    try:
        data = json.loads(mark.read_text(encoding="utf-8"))
    except ValueError:
        return "foreign"
    if not isinstance(data, dict) or data.get("tool") != "deploy/bundle/install_venvs.py":
        return "foreign"
    if not data.get("complete"):
        return "partial"
    plan = {k: v for k, v in data.items() if k != "complete"}
    return "same" if plan == want else "other"


def write_mark(venv: Path, want: dict, complete: bool) -> None:
    (venv / MARK).write_text(json.dumps(dict(want, complete=complete), ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")


# ---------------------------------------------------------------- 権限

def open_to_others(root: Path, top: Path, walk: bool, links: bool = True) -> "list[str]":
    """top（walk なら中の全部も。 リンクは追わない）のうち、 流した利用者の持ち物でない物と、 グループかほかの人が書ける物
    の、 root からの道の並び。 links が真なら、 リンクは持ち主だけを見る（venv の python は system の物を指す）。 偽なら、
    中のリンクを並びに入れる（pip に読ませる要求のファイルの置き場）。 読めない所があれば止める（照らせないまま
    進まない）。"""
    uid = os.geteuid()
    found: "list[str]" = []

    def fail(e: OSError) -> None:
        where = os.path.relpath(e.filename, root) if e.filename else os.path.relpath(top, root)
        raise Stop(f"{where} を読めないので、権限を照らせません（{type(e).__name__}）")

    def look(p: str) -> None:
        try:
            st = os.lstat(p)
        except OSError as e:
            fail(e)
        link = stat.S_ISLNK(st.st_mode)
        mode = 0 if link else stat.S_IMODE(st.st_mode)
        if link and not links and p != str(top):
            found.append(os.path.relpath(p, root) + "（リンク）")
        elif st.st_uid != uid or mode & OPEN_WRITE:
            found.append(os.path.relpath(p, root))

    look(str(top))
    if walk and top.is_dir() and not top.is_symlink():
        for dirpath, dirs, files in os.walk(top, onerror=fail):
            for name in dirs + files:
                look(os.path.join(dirpath, name))
    return found


def open_message(found: "list[str]") -> str:
    head = "・".join(found[:3]) + (f" ほか {len(found) - 3} 個" if len(found) > 3 else "")
    return (f"流した利用者のほかの人（グループを含む）が書き換えられる物か、ほかの人の持ち物か、置き場の中のリンクが"
            f"あります（{len(found)} 個: {head}）")


def check_own_places(root: Path) -> None:
    """入れる前の権限の照らし（何も作らない。 今ある物の権限は直さない）。"""
    venvs, cache = root / ".venvs", root / ".cache"
    logdir = cache / "install_venvs"
    for p in (venvs, cache, logdir):
        if p.is_symlink():
            raise Stop(f"{p.relative_to(root)} がリンクです（入れる道具は、リンクの先に作らず、使いません）")
    found: "list[str]" = []
    for p, walk in ((root, False), (venvs, False), (venvs / "web", False), (venvs / "ai", False), (cache, False),
                    (logdir, True)):
        if p.exists() or p.is_symlink():
            found += open_to_others(root, p, walk, links=p != logdir)
    if found:
        raise Stop(open_message(found) + "。書けた間に替えられたかもしれないので、権限を外すだけにせず、人が確かめます"
                   "（.venvs と .cache の物なら、消してから umask 022 で入れ直す）")


def python_version(python: str) -> "tuple[int, ...]":
    r = subprocess.run([python, "-I", "-c", "import sys; print(*sys.version_info[:3])"], capture_output=True, text=True)
    if r.returncode != 0:
        raise Stop(f"Python を流せません: {python}")
    return tuple(int(x) for x in r.stdout.split())


# ---------------------------------------------------------------- 計画と流れ

def plan(repo: Path, wheelhouse: Path, machine: Path, system_release: Path) -> dict:
    """入れる前の照らしをすべて済ませ、 計画を返す（何も作らない）。"""
    symbol, kind = read_machine(machine)
    versions = read_versions(repo)
    product = read_product(system_release)
    declaration = read_declaration(repo)
    if normalize(declaration["name"]) != normalize(product["name"]):
        raise Stop(f"System Release の product_package.name（{product['name']}）が、根の pyproject.toml の [project].name"
                   f"（{declaration['name']}）と違います（この木の製品のパッケージだけを入れる）")
    bundles, releases, sums = {}, {}, {}
    for key, short in CORES.items():
        v = versions[key]
        bundle = wheelhouse / short / v["tag"]
        releases[short] = check_bundle(bundle, sha256sums_sha256=v["sha256sums_sha256"], wheel_sha256=v["wheel_sha256"],
                                       label=f"{short} {v['tag']}", tag=v["tag"], commit=v["commit"])
        bundles[short], sums[short] = bundle, read_sums(bundle)
    bundle = wheelhouse / normalize(product["name"]) / product["version"]
    releases["product"] = check_bundle(bundle, sha256sums_sha256=None, wheel_sha256=product["wheel_sha256"],
                                       label=f"製品 {product['version']}", product=product)
    bundles["product"], sums["product"] = bundle, read_sums(bundle)
    made_for = releases["ia"].get("pdm")
    if not isinstance(made_for, dict) or made_for.get("tag") != versions["pdm"]["tag"] \
            or made_for.get("sha256sums_sha256") != versions["pdm"]["sha256sums_sha256"]:
        raise Stop("撮像側の束が前提にした PDM の束（撮像側の release.json の pdm の tag・sha256sums_sha256）が、"
                   "versions.yaml の pdm と違うか書いてありません（組合せの正本は撮像側の lock）")
    locks, lock_pins = {}, {}
    for k, name in IA_LOCKS.items():
        locks[k], lock_pins[k] = check_lock(bundles["ia"], name, sums["ia"])
    main_pin = {s: [*wheel_dist(releases[s]), None] for s in ("ia", "product")}
    camera = read_camera(wheelhouse)
    taken = {n for n, _, _ in lock_pins["web"]} | {main_pin["ia"][0], main_pin["product"][0]} | set(VENV_SEED)
    for c in camera:
        if c["name"] in taken:
            raise Stop(f"{CAMERA_DIR}/ の {c['file']} は、束か venv の種（pip・setuptools）で入れる配布物と同じ名前です"
                       "（カメラの部品だけを置く。束の物は入れ替えない）")
    pins = {"web": merge_pins(".venvs/web", lock_pins["web"], [main_pin["ia"]], [main_pin["product"]],
                              [[c["name"], c["version"], None] for c in camera]),
            "ai": merge_pins(".venvs/ai", wheel_pins(sums["pdm"]), [main_pin["ia"]], lock_pins["guard"],
                             [main_pin["product"]])}
    want = {"tool": "deploy/bundle/install_venvs.py", "schema": SCHEMA, "machine": symbol, "kind": kind,
            "pdm": {"tag": versions["pdm"]["tag"], "sha256sums_sha256": versions["pdm"]["sha256sums_sha256"]},
            "ia": {"tag": versions["image_acquisition_pdm"]["tag"],
                   "sha256sums_sha256": versions["image_acquisition_pdm"]["sha256sums_sha256"]},
            "product": {"name": product["name"], "version": product["version"],
                        "wheel_sha256": product["wheel_sha256"]}}
    return {"bundles": bundles, "releases": releases, "sums": sums, "locks": locks, "pins": pins, "want": want,
            "camera": camera, "camera_dir": wheelhouse / CAMERA_DIR, "declaration": declaration}


def venv_want(p: dict, name: str) -> dict:
    """その venv の計画（印に書く物）。 カメラの部品は置いたときだけ書く（置かない機体の印は前と同じ）。 入れるのは Web の
    venv だけだが、 起動の命令 serve が Web と判定の印を venv の鍵のほかは同じかで比べるので、 両方に同じ物を書く。"""
    want = dict(p["want"], venv=name)
    if p["camera"]:
        want["camera"] = p["camera"]
    return want


def build_web(run, venv: Path, p: dict, tmp: Path) -> None:
    py = venv / "bin" / "python"
    ia, product = p["bundles"]["ia"], p["bundles"]["product"]
    pip_install(run, py, p["locks"]["web"], [ia / "wheelhouse"])
    (tmp / "ia.txt").write_text(wheel_requirements(ia, p["sums"]["ia"], p["releases"]["ia"]["wheel"]), encoding="utf-8")
    pip_install(run, py, tmp / "ia.txt", [ia / "wheelhouse"], no_deps=True)
    (tmp / "product.txt").write_text(
        wheel_requirements(product, p["sums"]["product"], p["releases"]["product"]["wheel"]), encoding="utf-8")
    pip_install(run, py, tmp / "product.txt", [product / "wheelhouse"], no_deps=True)
    if p["camera"]:
        # もう入った配布物のファイルを上書きするか、ほかの配布物の頭のフォルダ（Core のパッケージなど）に置く wheel は断る
        # （入れる前に、 venv の配布物の RECORD の道と比べる。 道の形は camera_wheel_files が崩れた物を断ってある）
        have = set(json.loads(run([str(py), "-I", "-c", _FILES], capture=True) or "[]"))
        tops = {h.split("/", 1)[0] for h in have if "/" in h and not h.split("/", 1)[0].endswith((".dist-info", ".data"))}
        for c in p["camera"]:
            files = camera_wheel_files(p["camera_dir"] / c["file"])
            clash = sorted(set(files) & have)
            if clash:
                raise Stop(f"{CAMERA_DIR}/ の {c['file']} が、もう入った配布物のファイルを上書きします（{clash[0]} など。"
                           "カメラの部品は入れていません）")
            shared = sorted({f.split("/", 1)[0] for f in files if "/" in f} & tops)
            if shared:
                raise Stop(f"{CAMERA_DIR}/ の {c['file']} が、ほかの配布物の頭のフォルダ {shared[0]}/ に置きます"
                           "（カメラの部品は入れていません）")
        # 依存を許して入れる（index は使わず、 置き場は camera/ だけ）。 依存がもう入った物と camera/ の wheel で満たせなければ、
        # pip は何も入れずに止まる（Web の venv の numpy などと組めない）
        (tmp / "camera.txt").write_text("".join(f"{c['name']}=={c['version']} --hash=sha256:{c['sha256']}\n"
                                                for c in p["camera"]), encoding="utf-8")
        try:
            pip_install(run, py, tmp / "camera.txt", [p["camera_dir"]])
        except Stop as e:
            raise Stop(f"人が置いたカメラの部品が、Web の venv に入った物と組めません（依存が足りないか版が合いません。"
                       f"カメラの部品は入れていません）: {e}") from None


def build_ai(run, venv: Path, p: dict, tmp: Path) -> None:
    py = venv / "bin" / "python"
    pdm, ia, product = p["bundles"]["pdm"], p["bundles"]["ia"], p["bundles"]["product"]
    (tmp / "pdm.txt").write_text(wheel_requirements(pdm, p["sums"]["pdm"]), encoding="utf-8")
    pip_install(run, py, tmp / "pdm.txt", [pdm / "wheelhouse"])
    (tmp / "ia.txt").write_text(wheel_requirements(ia, p["sums"]["ia"], p["releases"]["ia"]["wheel"]), encoding="utf-8")
    pip_install(run, py, tmp / "ia.txt", [ia / "wheelhouse"], no_deps=True)
    pip_install(run, py, p["locks"]["guard"], [pdm / "wheelhouse", ia / "wheelhouse"])
    (tmp / "product.txt").write_text(
        wheel_requirements(product, p["sums"]["product"], p["releases"]["product"]["wheel"]), encoding="utf-8")
    pip_install(run, py, tmp / "product.txt", [product / "wheelhouse"], no_deps=True)


def wheel_dist(release: dict) -> "tuple[str, str]":
    """release.json の本体の wheel の（配布物の名前, 版）。 名前は決め打ちせず、 wheel の名前から取る。"""
    parts = str(release["wheel"])[:-4].split("-")
    return normalize(parts[0]), parts[1]


def expectations(name: str, p: dict) -> dict:
    """入れた後に照らす物（束のままの名前と版・依存の組・部品の登録・doctor を呼ぶ Python の名前）。 extra と部品の登録は
    根の pyproject.toml の宣言から（read_declaration。 部品の登録の宣言が無ければ照らさない）。"""
    r = p["releases"]
    declaration = p["declaration"]
    product = wheel_dist(r["product"])
    want = {"pins": p["pins"][name], "seed": list(VENV_SEED), "extras": [], "judge": name == "ai",
            "package": declaration["package"]}
    if name == "ai":
        pdm = wheel_dist(r["pdm"])
        want["extras"] = [[pdm[0], "web"]] + [[product[0], extra] for extra in declaration["extras"]]
        if declaration["parts"]:
            want["parts"] = [product[0], declaration["parts"]]
    return want


def main(argv: "list[str] | None" = None, repo: Path = REPO, make_runner=None) -> int:
    ap = argparse.ArgumentParser(description="据え付けた機体で、 配布物の束から .venvs/web・.venvs/ai を作る")
    ap.add_argument("--machine", required=True, help="機体の設定（machines/<記号>.yaml。 kind が installed か shown）")
    ap.add_argument("--system-release", required=True, help="System Release の記録（product_package を読む）")
    ap.add_argument("--root", help="案件のフォルダ（既定はこの Repository の 1 つ上）。 .venvs/ をここに作る")
    ap.add_argument("--wheelhouse", help="束の置き場（既定は <root>/wheelhouse）")
    ap.add_argument("--python", default=shutil.which("python3.10") or "", help="venv を作る Python 3.10")
    ap.add_argument("--check", action="store_true", help="何も作らずに、 照らしと計画だけを出す")
    ap.add_argument("--rebuild", action="store_true", help="この道具の印があり、 計画と違うか途中の venv を作り直す")
    opts = ap.parse_args(argv)
    make_runner = make_runner or Runner
    # 流す間（venv と pip の子にも）、 グループとほかの人の書き込みを外す（流す人がもっと絞った umask はそのまま）
    old_umask = os.umask(UMASK)
    os.umask(old_umask | UMASK)
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise Stop("root で流さないでください")
        if opts.check and opts.rebuild:
            raise Stop("--check と --rebuild は一緒に使えません")
        root = Path(opts.root).resolve() if opts.root else repo.parent
        wheelhouse = Path(opts.wheelhouse).resolve() if opts.wheelhouse else root / "wheelhouse"
        p = plan(repo, wheelhouse, Path(opts.machine), Path(opts.system_release))
        say(f"機体 {p['want']['machine']}（{p['want']['kind']}）・PDM {p['want']['pdm']['tag']}・撮像側 "
            f"{p['want']['ia']['tag']}・製品 {p['want']['product']['version']}: 束の照らしは合いました")
        for c in p["camera"]:
            say(f"カメラの部品（人が {CAMERA_DIR}/ に置いた物。Web の venv に入れる）: {c['name']} {c['version']}・"
                f"sha256 {c['sha256']}（配置の記録に写す）")
        if not p["camera"] and p["camera_dir"].is_dir():
            say(f"{CAMERA_DIR}/ に wheel がありません（カメラの部品は入れません）")
        if not opts.check:
            if not opts.python:
                raise Stop("--python に Python 3.10 を渡してください（PATH に python3.10 がありません）")
            if python_version(opts.python)[:2] != PYTHON:
                raise Stop(f"--python が Python {PYTHON[0]}.{PYTHON[1]} ではありません")
        check_own_places(root)
        venvs = root / ".venvs"
        logdir = root / ".cache" / "install_venvs"
        states = {}
        for name in ("web", "ai"):
            want = venv_want(p, name)
            states[name] = venv_state(venvs / name, want)
            say(f".venvs/{name}: {dict(none='まだ無い', same='今の計画と同じ', other='別の計画で作った', partial='途中で止まった', foreign='この道具が作った物ではない')[states[name]]}")
            if states[name] == "foreign":
                raise Stop(f".venvs/{name} はこの道具が作った venv ではありません（消すかは人が決めます）")
            if states[name] in ("other", "partial") and not (opts.rebuild or opts.check):
                raise Stop(f".venvs/{name} は作り直しが要ります（--rebuild で、この道具が作った venv だけを作り直します）")
        if opts.check:
            ok = True
            for name in ("web", "ai"):
                if states[name] == "same":
                    # 先に歩いて照らし、 ほかの人が書ける物があれば、 その venv の python を流さない
                    found = open_to_others(root, venvs / name, True)
                    bad = [open_message(found)] if found else \
                        verify(make_runner(None), venvs / name / "bin" / "python", expectations(name, p))
                    say(f".venvs/{name} の照らし: {'合う' if not bad else '合わない'}")
                    for b in bad:
                        say(f"  {b}")
                    ok = ok and not bad
                elif states[name] != "none":
                    ok = False
            say("--check なので何も作っていません")
            return 0 if ok else 1
        logdir.mkdir(parents=True, exist_ok=True)
        run = make_runner(logdir / "install.log")
        for name, build in (("web", build_web), ("ai", build_ai)):
            venv, want = venvs / name, venv_want(p, name)
            if states[name] in ("other", "partial"):
                shutil.rmtree(venv)
                say(f".venvs/{name} を消しました（この道具が作った物）")
            if states[name] != "same":
                say(f".venvs/{name} を作ります（index を使わず、hash で照らして入れます）")
                venvs.mkdir(parents=True, exist_ok=True)
                tmp = logdir / f"req-{name}"
                tmp.mkdir(exist_ok=True)
                run([opts.python, "-I", "-m", "venv", str(venv)], env=pip_env())
                write_mark(venv, want, complete=False)          # 途中で落ちても、 --rebuild で作り直せる
                build(run, venv, p, tmp)
            # venv と、 pip に読ませた要求のファイルの置き場を、 全部歩いて照らす。 先に歩き、 ほかの人が書ける物が
            # あれば、 その venv の python を流さない
            found = open_to_others(root, venv, True) + open_to_others(root, logdir, True, links=False)
            bad = [open_message(found)] if found else verify(run, venv / "bin" / "python", expectations(name, p))
            write_mark(venv, want, complete=not bad)
            if bad:
                for b in bad:
                    say(f"  合わない: {b}")
                raise Stop(f".venvs/{name} の照らしが通りません（印を途中に戻しました。直してから --rebuild で作り直す）")
            say(f".venvs/{name}: 照らしが通りました")
        say("できました")
        return 0
    except Stop as e:
        print(f"[install] 止めます: {e}", file=sys.stderr)
        return 1
    finally:
        os.umask(old_umask)


if __name__ == "__main__":
    sys.exit(main())

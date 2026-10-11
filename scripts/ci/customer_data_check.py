#!/usr/bin/env python3
"""**追跡しているファイルに、 現場の値が混ざっていないか**を見る。

顧客の値が Core に残ると、 納品テンプレートへ同梱されて他の現場へ
配られる。 PDM 側の `configs/README.md` に「実際に起きた」と記録がある。

見るのは **git が追跡しているもの + まだ add していない新しいもの**。 機体の上に現場の設定があるのは
正しい状態なので、 追跡外は対象にしない。

    python scripts/ci/customer_data_check.py
    python scripts/ci/customer_data_check.py --registry <リポジトリの外の登録表>
    python scripts/ci/customer_data_check.py --require-registry     # 登録表を必ず使うとき

見つけるもの:

    ・現場で使う帯域の IP（192.168.x / 10.x / 172.16-31.x / 169.254.x）
    ・カメラのシリアルらしい並び（英大文字 2〜4 字のあとに数字が 8 字以上）
    ・顧客に固有の語（**登録表に書いた語だけ**。 下を参照）

**見本は通す。** `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に
予約されたアドレスで、 繋ぎに行っても届かない。 見本にはこれを使う。

**見つけた値は出力しない。** 出すのは種類・ファイル・行だけ（IP・シリアル・
顧客の語のどれも同じ。 ファイルの道に値が入っていれば、 そこも伏せる）。
CI の記録は外から見えることがあり、 公開のリポジトリでは誰でも読める。
値は、 手元でファイルの該当の行を開いて見る（ファイルと行が分かれば
足りるので、 値を出す切り替えも作らない）。

**顧客の名前は、 この script に書かない。** この script の正本は公開の
`MirumeAI/.github` にあり、 名前を書けば顧客の名前を公開することになる。
名前から作ったハッシュも置かない（名前の候補を当てていけば元に戻せる）。
顧客に固有の語（名前・略称・案件名・機体の識別子など）は、 リポジトリの外の
**登録表** から読む。 探す順番:

    1. 引数 `--registry <ファイル>`
    2. 環境変数 `MIRUMEAI_CUSTOMER_REGISTRY_FILE`（登録表のファイルの場所）
    3. 環境変数 `MIRUMEAI_CUSTOMER_REGISTRY`（登録表の **中身**。 ファイルに
       書き出さず、 メモリの中で読む）
    4. `~/.config/mirumeai/customer_registry.txt`（あれば）

3 の値は、 `base64:` に続けて **登録表のファイルを base64 にした 1 行**:

    printf 'base64:%s' "$(base64 -w0 customer_registry.txt)"

**複数行のまま secret にしない。** GitHub は複数行の secret を 1 行ずつも
伏せ字にし（actions/runner の `Worker.cs`）、 それが同じ仕事のすべての段の
記録に掛かる。 語がほかの語の一部として記録に出ると「***」になり、 公開の
記録の伏せ字の位置から語を推し量れてしまう。 1 行の base64 なら、 伏せ字は
その 1 行だけに付く。 `base64:` で始まらない値は読まずに止める（中身を
そのまま貼る取り違えを、 黙って通さない）。

登録表は 1 行に 1 つの語を書く（`#` から後と空行は読まない）。 英字だけの
語は **語として**（前後に英字が続かないとき）大文字と小文字を区別せずに探し、
それ以外はそのままの並びで探す。 **顧客が居なくなっても、 登録表から
語を消さない**（過去の混入を見つけるため）。

  ・登録表がリポジトリの中にあれば断る（commit されるのを防ぐ）。 非公開の
    リポジトリで置くと決めたときだけ `--allow-registry-in-repo` で許す
  ・登録表の場所も中身も OS の例外の文も、 出力に書かない（登録表の何行目かと、
    決まった言葉だけを書く）。 場所の欄の値は **ファイルに触る前に形を確かめ**、
    改行や NUL などの制御文字を含む値・長すぎる値・`base64:` や `#` で始まる値は、
    中身の取り違えとして止める。 `--registry ""` も、 指定されたものとして止める。
    どれも終了コード 2
  ・登録表が無ければ語の照合を飛ばし、 **飛ばしたことを必ず出力する**
    （黙って合格にしない）。 `--require-registry` を付けると、 無いときに
    終了コード 2 で止める。 **登録表を渡す仕組みでは必ず付ける**（渡し損ねを
    合格にしない）

**登録表を、 PR の code が動く仕組みに渡さない。** 書き込み権限があれば、
workflow や試験を書き換えて secret を読み出せる。 通常の CI（pull_request）
には登録表を渡さず（名前は「照合していません」と出る）、 名前の照合は、
手元の登録表か、 main の検査だけが登録表を読み PR の中身をデータとして
扱う仕組みで行う。
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[2]

#: `check()` から見えるようにしておく（main で読み込む）。
_ALLOW_CACHE: "dict[str, str]" = {}

#: 見るファイル。 画像やフォントは中身を読まない。
_TEXT = {".json", ".py", ".md", ".txt", ".yml", ".yaml", ".html", ".js", ".sh",
         ".service", ".toml", ".cfg", ".ini"}

#: 文書用に予約されたアドレス（RFC 5737）。 見本はこれを使う。
_DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")

_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
#: 英大文字と数字が続く並び。 カメラのシリアルはこの形（英大文字 2〜4 字のあとに
#: 数字が 8 字以上）。 **例として実在の番号を書かない。**
_SERIAL = re.compile(r"\b[A-Z]{2,4}\d{8,}\b")

#: 登録表のファイルの場所を渡す環境変数。
REGISTRY_FILE_ENV = "MIRUMEAI_CUSTOMER_REGISTRY_FILE"
#: 登録表の **中身** を渡す環境変数（PR の code が動く CI には渡さない）。
#: 値は `base64:` + 登録表のファイルを base64 にした 1 行（上の説明を参照）。
REGISTRY_ENV = "MIRUMEAI_CUSTOMER_REGISTRY"
REGISTRY_ENV_PREFIX = "base64:"
DEFAULT_REGISTRY = Path("~/.config/mirumeai/customer_registry.txt")

#: 登録表を使えないので止めたとき（無い・見つからない・空・リポジトリの中にある）。
EXIT_REGISTRY = 2

#: 見つかっても構わないもの。 **理由を必ず書く。**
#: 対象外にするファイルは `.ci-leak-allow` に書く（`#` 以降が理由）。
#:
#: **script の中に持たせない。** Repository ごとに対象外が違うので、
#: 中に書くと script が Repository ごとに分かれ、 改善が届かなくなる
#: （実際に validator 4 種でそうなっていた）。
ALLOW_FILE = ".ci-leak-allow"


class Name:
    """登録表の 1 行。 **中身は repr に出さない**（例外の表示から記録に残さないため）。

    dataclass にしない。 `from __future__ import annotations` と組み合わさると、
    `sys.modules` に登録せずに読み込んだとき（importlib で直に読む試験など）に
    読み込みで落ちる。
    """

    __slots__ = ("line", "text", "_pat", "_nfc_pat")

    def __init__(self, line: int, text: str) -> None:
        self.line = line
        self.text = text
        self._pat = self._compile(text)
        # 出力の道を伏せるときは NFC に揃えて探す（NFD の道でも伏せる）。
        # 中身の照合は揃えない（照合の範囲を変えない）。
        nfc = unicodedata.normalize("NFC", text)
        self._nfc_pat = self._pat if nfc == text else self._compile(nfc)

    @staticmethod
    def _compile(text: str) -> "re.Pattern[str]":
        # 英字だけの語は **語として** 探す。 部分一致にすると、 語が
        # ほかの語の一部になっているところ（例: `fact` と `factory`）を拾う
        # （実在の名前で、 実際に 6 ファイルを誤検出した）。
        if text.isascii():
            return re.compile(
                r"(?<![A-Za-z])" + re.escape(text) + r"(?![A-Za-z])", re.I)
        return re.compile(re.escape(text))

    def __repr__(self) -> str:
        return f"Name(line={self.line})"

    def positions(self, s: str) -> "list[int]":
        """`s` の中で見つかった位置。 **見つかった文字列は返さない。**"""
        return [m.start() for m in self._pat.finditer(s)]


#: 登録表から読んだ語（main で読み込む）。 **script の中には書かない。**
_REGISTRY: "tuple[Name, ...]" = ()


class RegistryError(Exception):
    """登録表を使えない。 終了コード 2 で止める。

    **文は決まった言葉だけにする。** 登録表の場所・中身・OS の例外の文を
    入れない。 場所の欄へ中身を取り違えて渡しても、 場所に名前が入って
    いても、 記録に出ないように。
    """


#: 場所として受ける値の上限。 これを超える値、 改行や NUL などの制御文字を
#: 含む値、 `base64:` や `#` で始まる値は、 中身の取り違えとして **ファイルに
#: 触る前に** 止める（長い値を場所として触ると、 OS の例外の文に値が出る）。
_MAX_PATH_CHARS = 1024
_MAX_PATH_PART_BYTES = 255
#: 登録表のファイルの大きさの上限（語の一覧には十分に大きい）。
_MAX_REGISTRY_BYTES = 1_000_000


def _inside(path: Path, root: Path) -> bool:
    p, r = path.resolve(), root.resolve()
    return p == r or r in p.parents


def _looks_like_a_path(value: str) -> bool:
    """場所として受けた値を、 ファイルに触らずに確かめる。"""
    if not value.strip() or len(value) > _MAX_PATH_CHARS:
        return False
    if value.startswith((REGISTRY_ENV_PREFIX, "#")):
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return False
    return all(len(part.encode("utf-8", "surrogateescape")) <= _MAX_PATH_PART_BYTES
               for part in value.split("/"))


def parse_registry(text: str) -> "tuple[Name, ...]":
    """登録表の中身を読む。 1 行に 1 つ。 `#` から後と空行は読まない。"""
    names = []
    for i, ln in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        t = ln.partition("#")[0].strip()
        if t:
            names.append(Name(i, t))
    return tuple(names)


def _names(text: str, label: str) -> "tuple[Name, ...]":
    """語が 1 つも無い登録表は使わない（照合したつもりにさせない）。"""
    names = parse_registry(text)
    if not names:
        raise RegistryError(f"{label}に語がありません")
    return names


def load_registry(path: "str | os.PathLike[str]", *, root: "Path | None" = None,
                  allow_in_repo: bool = False,
                  label: str = "顧客の登録表") -> "tuple[Name, ...]":
    """登録表のファイルを読む。 `label` は、 エラーの文で登録表を指す決まった言葉。

    **どこで失敗しても、 文に場所も中身も OS の例外の文も入れない。** 確かめる順:
    場所の形 → 在るか・ファイルか → リポジトリの中か → 大きさ → 文字の形。
    「リポジトリの中にあります」は、 そこに本当にファイルがあるときだけ言う。
    """
    value = os.fspath(path)
    if not _looks_like_a_path(value):
        raise RegistryError(f"{label}の場所が正しくありません")
    root = ROOT if root is None else root
    try:
        p = Path(value).expanduser()
        st = p.stat()
        if not stat.S_ISREG(st.st_mode):
            raise RegistryError(f"{label}を読めません（ファイルではありません）")
        if not allow_in_repo and _inside(p, root):
            raise RegistryError(
                f"{label}がリポジトリの中にあります。 commit すると中身が外へ出るので、"
                " リポジトリの外へ置いてください（非公開のリポジトリで置くと決めた"
                "場合だけ --allow-registry-in-repo を付ける）")
        if st.st_size > _MAX_REGISTRY_BYTES:
            raise RegistryError(f"{label}の形式が正しくありません（大きすぎます）")
        data = p.read_bytes()
    except RegistryError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise RegistryError(f"{label}を読めません（無い・権限が無いなど）") from None
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RegistryError(f"{label}の形式が正しくありません（UTF-8 ではありません）") from None
    return _names(text, label)


def decode_registry(value: str, *, label: str = "顧客の登録表") -> "tuple[Name, ...]":
    """環境変数で渡された登録表（`base64:` + 1 行）を読む。 **中身はメモリの中だけ。**"""
    if not value.startswith(REGISTRY_ENV_PREFIX):
        raise RegistryError(
            f"{label}が {REGISTRY_ENV_PREFIX} で始まっていません（登録表のファイルを"
            " base64 にした 1 行を渡す。 複数行のまま渡さない）")
    try:
        text = base64.b64decode(value[len(REGISTRY_ENV_PREFIX):],
                                validate=True).decode("utf-8")
    except ValueError:
        raise RegistryError(f"{label}を base64 の登録表として読めません") from None
    return _names(text, label)


def _default_registry() -> "str | None":
    """決まった場所の登録表。 無ければ None。 在るのに確かめられないときは止める。"""
    try:
        p = DEFAULT_REGISTRY.expanduser()
    except RuntimeError:                       # HOME が分からない
        return None
    try:
        p.stat()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except (OSError, ValueError):
        raise RegistryError("決まった場所の登録表を読めません（権限が無いなど）") from None
    return str(p)


def resolve_registry(arg: "str | None", *, allow_in_repo: bool = False
                     ) -> "tuple[tuple[Name, ...], str] | None":
    """使う登録表と、 どこから読んだか（出力に書く決まった言葉）。 無ければ None。

    引数か環境変数で指定されたものが使えなければ止める（指定したのに
    黙って飛ばさない）。 **`--registry ""` のように空で指定されたときも、
    指定されたものとして止める**（ほかの探し方へ進まない）。 決まった場所は、
    あるときだけ使う。
    """
    if arg is not None:
        src = "引数 --registry の登録表"
        return load_registry(arg, allow_in_repo=allow_in_repo, label=src), src
    f = os.environ.get(REGISTRY_FILE_ENV, "").strip()
    if f:
        src = f"環境変数 {REGISTRY_FILE_ENV} の登録表"
        return load_registry(f, allow_in_repo=allow_in_repo, label=src), src
    content = os.environ.get(REGISTRY_ENV, "").strip()
    if content:
        src = f"環境変数 {REGISTRY_ENV} の登録表"
        return decode_registry(content, label=src), src
    default = _default_registry()
    if default is not None:
        src = "決まった場所の登録表"
        return load_registry(default, allow_in_repo=allow_in_repo, label=src), src
    return None


def _allow(root: Path) -> "dict[str, str]":
    """対象外のファイルと、 その理由。 **理由を書かせる。**"""
    f = root / ALLOW_FILE
    if not f.is_file():
        return {}
    out = {}
    for ln in f.read_text(encoding="utf-8").splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        path, _, why = ln.partition("#")
        path = path.strip()
        if path:
            out[path] = why.strip() or "（理由が書かれていません）"
    return out


_PRIVATE = re.compile(r"^(?:192\.168\.|10\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.)")


def tracked() -> list[Path]:
    """追跡しているファイル + **まだ追跡していない新しいファイル**。

    `git ls-files` だけを見ていたので、 **新しく作ったファイルは
    `git add` するまで検査されなかった**。 手元で「OK」と出たものが、
    commit した後の CI で初めて落ちる。 実際に 1 度そうなった
    （`control/config/ai_policy.py` の説明文に顧客名を書いていた）。

    `--others --exclude-standard` で、 `.gitignore` に入っているもの
    （顧客の設定そのもの）は除いたまま、 新しいファイルを足す。

    **`-z`（NUL 区切り）で読む。** 改行区切りのままだと、 日本語・引用符・
    バックスラッシュ・制御文字を含む道を git が表示用に引用符で囲んで返し、
    存在しない道として **黙って検査から外れていた**（IA の追跡ファイル
    25 件を見ていなかった）。 表示用の形を読み戻すことはしない。
    """
    seen, out = set(), []
    for args in (["git", "ls-files", "-z"],
                 ["git", "ls-files", "-z", "--others", "--exclude-standard"]):
        r = subprocess.run(args, cwd=ROOT, capture_output=True, check=True)
        for raw in r.stdout.split(b"\0"):
            if raw and raw not in seen:
                seen.add(raw)
                out.append(ROOT / os.fsdecode(raw))
    return out


def _line(s: str, pos: int) -> int:
    return s.count("\n", 0, pos) + 1


#: 出力の道を伏せるときの IP とシリアルの形。 中身の照合（`_IP`・`_SERIAL`）より
#: 境界を緩める（`_` や英字の隣、 重なった並びでも伏せる）。 出力にだけ使い、
#: 照合の範囲は変えない。
_PATH_IP = re.compile(r"(?<!\d)(?=((?:\d{1,3}\.){3}\d{1,3})(?!\d))")
_PATH_SERIAL = re.compile(r"(?<![A-Z0-9])(?=([A-Z]{2,4}\d{8,}))")


def _printable(s: str) -> str:
    """表示できない文字（制御文字など）を `\\xNN` の形にする（1 か所を 1 行で出す）。"""
    return "".join(c if c.isprintable() else f"\\x{ord(c):02x}" for c in s)


def _merged(spans: "list[tuple[int, int, str]]") -> "list[tuple[int, int, str]]":
    """重なる範囲を 1 つにまとめる。 印は、 まとめた中でいちばん長い範囲のもの。"""
    out: "list[tuple[int, int, str]]" = []
    for a, b, mark in sorted(spans):
        if out and a < out[-1][1]:
            pa, pb, pm = out[-1]
            out[-1] = (pa, max(pb, b), pm if pb - pa >= b - a else mark)
        else:
            out.append((a, b, mark))
    return out


def _shown(rel: str) -> str:
    """出力に書くファイルの道。 **道の中の値も伏せる**（顧客の名前を付けた
    ファイルなど。 道を出すと、 中身を伏せても名前が記録に出る）。

    **先に値を伏せ、 残りを表示できる形に直す**（逆の順にすると、 制御文字を
    置き換えた `\\xNN` の英数字が語の境界を壊し、 名前が出た）。 重なって
    見つかった値（長い語と短い語、 IP と語など）は 1 つにまとめて伏せ、
    欠片を残さない。 NFD の道も NFC に揃えてから探す。 UTF-8 でない道も
    落とさずに出す。
    """
    s = rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    s = unicodedata.normalize("NFC", s)
    spans = []
    for m in _PATH_IP.finditer(s):
        if _PRIVATE.match(m.group(1)):
            spans.append((m.start(), m.start() + len(m.group(1)), "＜IP＞"))
    for m in _PATH_SERIAL.finditer(s):
        spans.append((m.start(), m.start() + len(m.group(1)), "＜シリアル＞"))
    for n in _REGISTRY:
        for m in n._nfc_pat.finditer(s):
            spans.append((m.start(), m.end(), f"＜登録表の {n.line} 行目＞"))
    out, pos = [], 0
    for a, b, mark in _merged(spans):
        out += [_printable(s[pos:a]), mark]
        pos = b
    out.append(_printable(s[pos:]))
    return "".join(out)


def check(p: Path, registry: "tuple[Name, ...] | None" = None
          ) -> "list[tuple[int, str]]":
    """1 ファイルを見る。 見つけたものを（行, 種類）で返す。

    **見つけた値そのものは返さない**（出力に出さないため）。 `registry` を
    渡さなければ、 main で読んだ登録表を使う。
    """
    rel = str(p.relative_to(ROOT))
    if rel in _ALLOW_CACHE or p.suffix.lower() not in _TEXT:
        return []
    try:
        s = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = set()
    for m in _IP.finditer(s):
        ip = m.group(0)
        if ip.startswith(_DOC_NETS) or ip.startswith(("127.", "0.0.0.0", "255.")):
            continue
        if _PRIVATE.match(ip):
            found.add((_line(s, m.start()), "現場の IP らしきもの"))
    for m in _SERIAL.finditer(s):
        found.add((_line(s, m.start()), "シリアルらしき並び"))
    for n in (_REGISTRY if registry is None else registry):
        for pos in n.positions(s):
            found.add((_line(s, pos), f"顧客に固有の語（登録表の {n.line} 行目）"))
    return sorted(found)


class _Parser(argparse.ArgumentParser):
    """引数の誤りの文に、 渡された値を入れない（登録表の中身を位置の引数などへ
    取り違えて渡しても、 argparse の「unrecognized arguments: ...」で出さない）。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, "NG: 引数が正しくありません（--help で使い方を見る）\n")


def main() -> int:
    ap = _Parser(description=__doc__,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warn-only", action="store_true")
    ap.add_argument("--registry", metavar="FILE",
                    help="顧客の登録表（リポジトリの外のファイル）")
    ap.add_argument("--require-registry", action="store_true",
                    help="登録表が無ければ終了コード 2 で止める（登録表を渡す仕組みでは必ず付ける）")
    ap.add_argument("--allow-registry-in-repo", action="store_true",
                    help="登録表がリポジトリの中にあっても使う"
                         "（非公開のリポジトリで、 置くと決めた場合だけ）")
    a = ap.parse_args()

    global _ALLOW_CACHE, _REGISTRY
    _ALLOW_CACHE = _allow(ROOT)

    try:
        got = resolve_registry(a.registry, allow_in_repo=a.allow_registry_in_repo)
    except RegistryError as e:
        print(f"NG: {e}")
        return EXIT_REGISTRY
    except Exception:  # noqa: BLE001  予期しない誤りでも、 場所・中身・例外の文を出さない
        print("NG: 顧客の登録表を読み込めません（予期しない誤り。 場所と中身は出しません）")
        return EXIT_REGISTRY
    if got is None:
        if a.require_registry:
            print("NG: 顧客の登録表がありません（--require-registry）。 --registry、"
                  f" {REGISTRY_FILE_ENV}（ファイルの場所）、 {REGISTRY_ENV}"
                  f"（{REGISTRY_ENV_PREFIX} + base64 の 1 行）"
                  "のどれかで渡してください")
            return EXIT_REGISTRY
        _REGISTRY = ()
        names_note = ("顧客に固有の語: 登録表が無いので照合していません"
                      f"（--registry・{REGISTRY_FILE_ENV}・{REGISTRY_ENV} の"
                      "どれかで渡すと照合します）")
    else:
        _REGISTRY, src = got
        names_note = f"顧客に固有の語: {src}の {len(_REGISTRY)} 件と照合しました"

    files = tracked()
    bad: "dict[str, list[tuple[int, str]]]" = {}
    for p in files:
        hits = check(p)
        if hits:
            bad[str(p.relative_to(ROOT))] = hits

    print(f"検査: {len(files)} ファイル"
          "（追跡しているもの + まだ add していない新しいもの）"
          + (f" / 対象外 {len(_ALLOW_CACHE)}" if _ALLOW_CACHE else ""))
    print(names_note)
    if not bad:
        print("OK: 現場の値は見つかりませんでした")
        return 0
    # **値は出さない。** 種類・ファイル・行だけ（ファイルの道の中の値も伏せる）。
    for rel, hits in sorted(bad.items()):
        for line, kind in hits:
            print(f"  {kind}  file={_shown(rel)}  line={line}")
    places = sum(len(h) for h in bad.values())
    print(f"\nNG: {len(bad)} ファイル・{places} か所に、 現場の値らしきものがあります。")
    print("    値は出力しません（CI の記録は外から見えることがある）。 手元で"
          "ファイルの該当の行を開いて確かめてください。")
    print("    Repository には形と見本だけを置いてください。")
    print("    見本の IP は 192.0.2.x（文書用の予約アドレス）を使います。")
    return 0 if a.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())

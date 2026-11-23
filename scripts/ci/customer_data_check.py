#!/usr/bin/env python3
"""**追跡しているファイルに、 現場の値が混ざっていないか**を見る。

顧客の値が Core に残ると、 納品テンプレートへ同梱されて他の現場へ
配られる。 PDM 側の `configs/README.md` に「実際に起きた」と記録がある。

見るのは **git が追跡しているもの + まだ add していない新しいもの**。 機体の上に現場の設定があるのは
正しい状態なので、 追跡外は対象にしない。

    python scripts/ci/customer_data_check.py
    python scripts/ci/customer_data_check.py --warn-only     # 見つけても終了コード 0

見つけるもの:

    ・現場で使う帯域の IP（192.168.x / 10.x / 172.16-31.x / 169.254.x）
    ・カメラのシリアルらしい並び（英大文字 2〜4 字のあとに数字が 8 字以上）

**見本は通す。** `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に
予約されたアドレスで、 繋ぎに行っても届かない。 見本にはこれを使う。

**見つけた値は出力しない。** 出すのは種類・ファイル・行だけ（IP もシリアルも
同じ。 ファイルの道に値が入っていれば、 そこも伏せる）。
CI の記録は外から見えることがあり、 公開のリポジトリでは誰でも読める。
値は、 手元でファイルの該当の行を開いて見る（ファイルと行が分かれば
足りるので、 値を出す切り替えも作らない）。

**顧客の名前は照合しない。** Core は一般的な名前で実装し、 顧客の会社や
部品の名前を書かない（顧客について決まっている名前は、 顧客 Repository の
名前 `customer-<会社>-<部品>` だけ）。 名前の混入は、 その決まりとレビューで
防ぐ。 前はリポジトリの外の登録表（顧客の名前の一覧）と照らす形を持っていたが、
登録表を作らないと決めて外した（2026-11-23 の利用者の決定。
`MirumeAI/.github#209`）。 **この script にも顧客の名前を書かない**（正本は
公開の `MirumeAI/.github` にある）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
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

#: 見つかっても構わないもの。 **理由を必ず書く。**
#: 対象外にするファイルは `.ci-leak-allow` に書く（`#` 以降が理由）。
#:
#: **script の中に持たせない。** Repository ごとに対象外が違うので、
#: 中に書くと script が Repository ごとに分かれ、 改善が届かなくなる
#: （実際に validator 4 種でそうなっていた）。
ALLOW_FILE = ".ci-leak-allow"


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
    """出力に書くファイルの道。 **道の中の値も伏せる**（IP やシリアルを付けた
    ファイルなど。 道を出すと、 中身を伏せても値が記録に出る）。

    **先に値を伏せ、 残りを表示できる形に直す**（逆の順にすると、 制御文字を
    置き換えた `\\xNN` の英数字が値の境界を壊す）。 重なって見つかった値は
    1 つにまとめて伏せ、 欠片を残さない。 UTF-8 でない道も落とさずに出す。
    """
    s = rel.encode("utf-8", "surrogateescape").decode("utf-8", "replace")
    spans = []
    for m in _PATH_IP.finditer(s):
        if _PRIVATE.match(m.group(1)):
            spans.append((m.start(), m.start() + len(m.group(1)), "＜IP＞"))
    for m in _PATH_SERIAL.finditer(s):
        spans.append((m.start(), m.start() + len(m.group(1)), "＜シリアル＞"))
    out, pos = [], 0
    for a, b, mark in _merged(spans):
        out += [_printable(s[pos:a]), mark]
        pos = b
    out.append(_printable(s[pos:]))
    return "".join(out)


def check(p: Path) -> "list[tuple[int, str]]":
    """1 ファイルを見る。 見つけたものを（行, 種類）で返す。

    **見つけた値そのものは返さない**（出力に出さないため）。
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
    return sorted(found)


class _Parser(argparse.ArgumentParser):
    """引数の誤りの文に、 渡された値を入れない（値を位置の引数などへ取り違えて
    渡しても、 argparse の「unrecognized arguments: ...」で出さない）。 外した
    登録表の引数（`MirumeAI/.github#209`）を渡したときも、 合格にせずにここで止まる。"""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(2, "NG: 引数が正しくありません（--help で使い方を見る）\n")


def main() -> int:
    ap = _Parser(description=__doc__,
                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warn-only", action="store_true")
    a = ap.parse_args()

    global _ALLOW_CACHE
    _ALLOW_CACHE = _allow(ROOT)

    files = tracked()
    bad: "dict[str, list[tuple[int, str]]]" = {}
    for p in files:
        hits = check(p)
        if hits:
            bad[str(p.relative_to(ROOT))] = hits

    print(f"検査: {len(files)} ファイル"
          "（追跡しているもの + まだ add していない新しいもの）"
          + (f" / 対象外 {len(_ALLOW_CACHE)}" if _ALLOW_CACHE else ""))
    print("顧客の名前: 照合しません（Core は一般的な名前で実装する決まり）")
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

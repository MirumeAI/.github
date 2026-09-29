#!/usr/bin/env python3
"""**追跡しているファイルに、 現場の値が混ざっていないか**を見る。

顧客の値が Core に残ると、 納品テンプレートへ同梱されて他の現場へ
配られる。 PDM 側の `configs/README.md` に「実際に起きた」と記録がある。

見るのは **git が追跡しているもの + まだ add していない新しいもの**。 機体の上に現場の設定があるのは
正しい状態なので、 追跡外は対象にしない。

    python scripts/ci/customer_data_check.py

見つけるもの:

    ・現場で使う帯域の IP（192.168.x / 10.x / 172.16-31.x / 169.254.x）
    ・カメラのシリアルらしい並び
    ・顧客の名前

**見本は通す。** `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に
予約されたアドレスで、 繋ぎに行っても届かない。 見本にはこれを使う。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: `check()` から見えるようにしておく（main で読み込む）。
_ALLOW_CACHE: "dict[str, str]" = {}

#: 見るファイル。 画像やフォントは中身を読まない。
_TEXT = {".json", ".py", ".md", ".txt", ".yml", ".yaml", ".html", ".js", ".sh",
         ".service", ".toml", ".cfg", ".ini"}

#: 文書用に予約されたアドレス（RFC 5737）。 見本はこれを使う。
_DOC_NETS = ("192.0.2.", "198.51.100.", "203.0.113.")

_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
#: 英大文字と数字が続く並び。 カメラのシリアルはこの形（例 FBE26060513）。
_SERIAL = re.compile(r"\b[A-Z]{2,4}\d{8,}\b")

#: 顧客の名前。 **足すときは、 その顧客がもう居なくなっても消さない**
#: （過去の混入を見つけるため）。
#:
#: 英字は **語として**一致させる。 部分一致にすると `recip` が `recipe` を
#: 拾う（実際に 6 ファイルで誤検出した）。
_NAMES_JA = ("三重精機",)
_NAME_WORDS = re.compile(
    r"(?<![A-Za-z])(?:mieseiki|lecip|recip)(?![A-Za-z])", re.I)

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
    """
    seen, out = [], []
    for args in (["git", "ls-files"],
                 ["git", "ls-files", "--others", "--exclude-standard"]):
        r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           check=True)
        for f in r.stdout.splitlines():
            if f and f not in seen:
                seen.append(f)
                out.append(ROOT / f)
    return out


def check(p: Path) -> list[str]:
    rel = str(p.relative_to(ROOT))
    if rel in _ALLOW_CACHE or p.suffix.lower() not in _TEXT:
        return []
    try:
        s = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = []
    for ip in sorted(set(_IP.findall(s))):
        if ip.startswith(_DOC_NETS) or ip.startswith(("127.", "0.0.0.0", "255.")):
            continue
        if _PRIVATE.match(ip):
            found.append(f"現場の IP らしきもの: {ip}")
    for sn in sorted(set(_SERIAL.findall(s))):
        found.append(f"シリアルらしきもの: {sn}")
    for n in _NAMES_JA:
        if n in s:
            found.append(f"顧客の名前: {n}")
    for n in sorted({m.group(0).lower() for m in _NAME_WORDS.finditer(s)}):
        found.append(f"顧客の名前: {n}")
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--warn-only", action="store_true")
    a = ap.parse_args()

    global _ALLOW_CACHE
    _ALLOW_CACHE = _allow(ROOT)

    files = tracked()
    bad: dict[str, list[str]] = {}
    for p in files:
        hits = check(p)
        if hits:
            bad[str(p.relative_to(ROOT))] = hits

    print(f"検査: {len(files)} ファイル"
          "（追跡しているもの + まだ add していない新しいもの）"
          + (f" / 対象外 {len(_ALLOW_CACHE)}" if _ALLOW_CACHE else ""))
    if not bad:
        print("OK: 現場の値は見つかりませんでした")
        return 0
    for rel, hits in sorted(bad.items()):
        print(f"  {rel}")
        for h in sorted(set(hits)):
            print(f"      {h}")
    print(f"\nNG: {len(bad)} ファイルに現場の値が入っています。")
    print("    Repository には形と見本だけを置いてください。")
    print("    見本の IP は 192.0.2.x（文書用の予約アドレス）を使います。")
    return 0 if a.warn_only else 1


if __name__ == "__main__":
    sys.exit(main())

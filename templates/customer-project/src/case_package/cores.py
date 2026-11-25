"""製品のパッケージが組める Core の版の範囲と、 その照らし。

正本は MirumeAI/development-docs の `03_customer_development/recipes_and_machines.md` の「製品のパッケージの入れ方」。

- 範囲はこの表だけに持つ。 `pyproject.toml` に Core の名前を書かない（`tests/product/test_pyproject.py`）
- 使う版の正本は `versions.yaml`。 Core を選ぶ Pull Request で、 選んだ版を含む範囲を書く（下限は出荷の組合せの版、
  上限は次の 2 つ目の数。 `tests/product/test_doctor.py` が照らす）。 書くまでは未選択（None）で、 照らしは通さない
- 版は site-packages の dist-info の `METADATA` を文字として読む（Core を読み込まない）。 無い・2 つ以上・読めない・
  版の形が分からないときは止める。 比べ方は PEP 440（`<H` は H の開発の版と前の版を入れない）
- 照らしを飛ばす引数や環境変数は作らない
"""
from __future__ import annotations

import re
from pathlib import Path

#: 撮像側の配布物の名前
DIST_IA = "mirumeai-image-acquisition"
#: PDM の配布物の名前
DIST_PDM = "mirumeai-pdm"

#: 配布物 → （下限〔含む〕, 上限〔含まない〕）。 未選択は None
RANGES: "dict[str, tuple[str, str] | None]" = {
    DIST_IA: None,
    DIST_PDM: None,
}

#: 正規化された PEP 440 の版（epoch は受けない）: 数の並び・前の版（a・b・rc）・後の版・開発の版・局所の印
_VERSION = re.compile(r"(?P<release>[0-9]+(?:\.[0-9]+)*)"
                      r"(?:(?P<pre>a|b|rc)(?P<pre_n>[0-9]+))?"
                      r"(?:\.post(?P<post>[0-9]+))?"
                      r"(?:\.dev(?P<dev>[0-9]+))?"
                      r"(?:\+(?P<local>[a-z0-9]+(?:\.[a-z0-9]+)*))?")
_PRE = {"a": 0, "b": 1, "rc": 2}
#: 開発の版でない物の並びの位置（どの開発の版の番号より大きい）
_NOT_DEV = 1 << 62


class CoreVersionError(Exception):
    """Core の版を読めない・形が分からない・範囲の外（通さずに止める）。"""


class CoreMissing(CoreVersionError):
    """Core が入っていない。"""


def _parse(text: str) -> "tuple[tuple, tuple[int, ...], bool]":
    """版の文字を、 並べる鍵・数の並び（末尾の 0 を落とした物）・開発の版か前の版かに分ける。"""
    m = _VERSION.fullmatch(text)
    if m is None:
        raise CoreVersionError(f"版の形が分かりません: {text!r}")
    release = tuple(int(x) for x in m.group("release").split("."))
    while len(release) > 1 and release[-1] == 0:
        release = release[:-1]
    pre, dev, post = m.group("pre"), m.group("dev"), m.group("post")
    if pre is None and post is None and dev is not None:
        pre_key: tuple = (-1, 0)                    # 開発の版だけの物は、 その版の前の版より前に並ぶ
    elif pre is None:
        pre_key = (3, 0)
    else:
        pre_key = (_PRE[pre], int(m.group("pre_n")))
    key = (release, pre_key, -1 if post is None else int(post), _NOT_DEV if dev is None else int(dev))
    return key, release, (pre is not None or dev is not None)


def in_range(version: str, low: str, high: str) -> bool:
    """`version` が `>=low,<high` に入るか（PEP 440。 局所の印は見ない）。 形が分からなければ CoreVersionError。"""
    key, release, unstable = _parse(version)
    low_key, _, low_unstable = _parse(low)
    high_key, high_release, high_unstable = _parse(high)
    if low_unstable or high_unstable:
        raise CoreVersionError("範囲の端は、 開発の版でも前の版でもない版で書きます")
    if key < low_key or not key < high_key:
        return False
    return not (unstable and release == high_release)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_version(site_packages: Path, dist: str) -> str:
    """site-packages に入っている配布物の版（dist-info の `METADATA` を文字として読む）。"""
    want = _norm(dist)
    try:
        infos = sorted(p for p in Path(site_packages).glob("*.dist-info") if _norm(p.name.split("-", 1)[0]) == want)
    except OSError as e:
        raise CoreVersionError(f"{dist}: site-packages を読めません（{type(e).__name__}）") from None
    if not infos:
        raise CoreMissing(f"{dist} が入っていません")
    if len(infos) > 1:
        raise CoreVersionError(f"{dist} の記録（dist-info）が {len(infos)} つあります（1 つにしてください）")
    name = version = ""
    try:
        for line in (infos[0] / "METADATA").read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                break
            if line.startswith("Name:") and not name:
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Version:") and not version:
                version = line.split(":", 1)[1].strip()
    except OSError as e:
        raise CoreVersionError(f"{dist} の記録を読めません（{type(e).__name__}）") from None
    if _norm(name) != want:
        raise CoreVersionError(f"{dist} の記録の名前が違います（{name or '無い'}）")
    if not version:
        raise CoreVersionError(f"{dist} の版を読めません")
    return version


def check(site_packages: Path, dists: "tuple[str, ...]") -> "tuple[list[str], list[str]]":
    """名指しした Core の版を照らす。 （合った物の行, 合わない物の行）。"""
    ok, bad = [], []
    for dist in dists:
        rng = RANGES.get(dist)
        if rng is None:
            bad.append(f"{dist}: 組める版の範囲が未選択です（versions.yaml で Core を選ぶ Pull Request で、"
                       " cores.py の RANGES に書く）")
            continue
        try:
            version = installed_version(site_packages, dist)
            if in_range(version, *rng):
                ok.append(f"{dist} {version}（範囲 >={rng[0]},<{rng[1]}）")
            else:
                bad.append(f"{dist} {version} は、 この製品のパッケージが組める範囲（>={rng[0]},<{rng[1]}）の外です")
        except CoreVersionError as e:
            bad.append(str(e))
    return ok, bad

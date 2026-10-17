#!/usr/bin/env python3
"""Product Release の識別を確かめる（各 Core の Release の workflow が使う共通の確かめ）。

**新しい Product Release は、 版の文字・注釈つきの動かさないタグ・そのタグが指す
commit が 1 対 1 に対応する。** 決まりの正本は development-docs の
「ReleaseとDeploymentの記録」の「Product Release の識別」。 ここはその機械の確かめ。

    python3 release_identity_check.py check --tag vX.Y.Z --version-source {pyproject,VERSION} \\
        --first-version X.Y.Z [--repo-root .] [--main-ref origin/main] [--product NAME] \\
        [--releases-json FILE] [--ci-run-url URL] [--notes-out FILE] [--github-output FILE]
    python3 release_identity_check.py drift --first-version X.Y.Z --releases-json FILE [--repo-root .]
    python3 release_identity_check.py preflight --tag vX.Y.Z --commit SHA \\
        --version-source {pyproject,VERSION} --first-version X.Y.Z [--repo-root .] \\
        [--main-ref origin/main] [--releases-json FILE]

check（タグの push を受けた CI。 落ちたら Release を作らない）:
  1. タグの形が `vX.Y.Z`（rc・接頭辞・`.devN` を付けない）
  2. 版が `--first-version` 以上（それより前の番号は LEGACY が使っている）
  3. 注釈つきのタグ
  4. タグの commit が main の祖先
  5. タグの commit の版の出どころが `X.Y.Z` と完全に一致し、 開発の版ではない
  6. `--releases-json` があれば、 下の drift も行う
  前の Product Release は、 1〜4 を満たす前のタグのうち、 この commit の祖先で版がいちばん
  大きいもの（`--releases-json` があれば、 GitHub Release のあるタグだけ）。 LEGACY は使わない。

drift（次の Release の前）:
  新しい形の Release ごとに、 ノートに書いた `tag object` と `commit` が今のタグと同じかを照らす。

preflight（人がタグを打つ前。 Agent が確かめてよい。 タグは打たない）:
  タグがまだ無いこと・commit が main の上にあること・その commit の版が一致すること・drift。

**顧客の値と名前を扱わない。** 出すのは版・`vX.Y.Z` の形のタグ・SHA・誤りの種類だけ。
形の違うタグ（案件の接頭辞つきなど）の名前は出さない。
終了コード: 0 合格、 1 不合格、 2 使い方の誤り。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: 版の数は ASCII の数字だけで、 先頭に 0 を付けない（0 そのものは可）。 こうすると、 同じ版を
#: 名乗る別の書き方（v0.04.0・v00.4.0・全角の数字など）が生まれず、 文字の完全な一致で比べられる。
_N = r"(0|[1-9][0-9]*)"
TAG_RE = re.compile(rf"v{_N}\.{_N}\.{_N}")
RELEASE_RE = re.compile(rf"{_N}\.{_N}\.{_N}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
NOTE_TAG_OBJECT = re.compile(r"^- tag object: ([0-9a-f]{40})\s*$", re.M)
NOTE_COMMIT = re.compile(r"^- commit: ([0-9a-f]{40})\s*$", re.M)


class Fail(Exception):
    """確かめに落ちた。 文には値を入れない（版と vX.Y.Z の形のタグと SHA だけ）。"""


def git(root: Path, *args: str) -> "tuple[int, str]":
    r = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                       env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    return r.returncode, r.stdout


def parse_release(text: str) -> "tuple[int, int, int] | None":
    m = RELEASE_RE.fullmatch(text)
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def parse_tag(tag: str) -> "tuple[int, int, int] | None":
    m = TAG_RE.fullmatch(tag)
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def shown(tag: str) -> str:
    """出してよい形のタグだけを出す（形の違うタグの名前には顧客の識別子が入りうる）。"""
    return tag if parse_tag(tag) else "<形の違うタグ>"


def pyproject_version(text: str) -> str:
    try:
        import tomllib  # Python 3.11 から
    except ImportError:
        tomllib = None  # type: ignore[assignment]
    if tomllib is not None:
        try:
            return str(tomllib.loads(text).get("project", {}).get("version", "") or "")
        except Exception:
            return ""
    m = re.search(r"(?ms)^\[project\][ \t]*$(.*?)(?=^\[|\Z)", text)
    if not m:
        return ""
    v = re.search(r'(?m)^version\s*=\s*"([^"]*)"\s*$', m.group(1))
    return v.group(1) if v else ""


def declared_version(root: Path, commit: str, source: str) -> str:
    """その commit の版の出どころの中身。 読めなければ空。"""
    path = "pyproject.toml" if source == "pyproject" else "VERSION"
    rc, text = git(root, "show", f"{commit}:{path}")
    if rc != 0:
        return ""
    if source == "pyproject":
        return pyproject_version(text)
    lines = text.split("\n")
    if len(lines) > 2 or (len(lines) == 2 and lines[1] != ""):
        return ""  # 版だけを 1 行で書く決まり（basic_checks.py と同じ）
    return lines[0].strip()


def tag_info(root: Path, tag: str) -> "tuple[str, str, str] | None":
    """(種類, タグの object, commit)。 タグが無ければ None。"""
    rc, out = git(root, "for-each-ref", f"refs/tags/{tag}",
                  "--format=%(objecttype) %(objectname) %(*objectname)")
    line = out.strip()
    if rc != 0 or not line or "\n" in line:
        return None
    parts = line.split()
    typ, obj = parts[0], parts[1]
    commit = parts[2] if typ == "tag" and len(parts) > 2 else obj
    return typ, obj, commit


def is_ancestor(root: Path, a: str, b: str) -> bool:
    return git(root, "merge-base", "--is-ancestor", a, b)[0] == 0


def resolve(root: Path, ref: str) -> str:
    rc, out = git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out.strip() if rc == 0 else ""


def load_releases(path: "str | None") -> "list[dict] | None":
    """GitHub の Release の一覧（REST の形）。

    `gh api --paginate` は配列を続けて出す（`[...][...]`）。 1 行に 1 つの JSON でもよい。
    続いた JSON の値を順に読み、 配列は開いて 1 つの一覧にする。
    """
    if not path:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    dec, i, out = json.JSONDecoder(), 0, []
    while True:
        while i < len(raw) and raw[i].isspace():
            i += 1
        if i >= len(raw):
            break
        val, i = dec.raw_decode(raw, i)
        out.extend(val if isinstance(val, list) else [val])
    if not all(isinstance(x, dict) for x in out):
        raise ValueError("Release の一覧の形ではない")
    return out


def new_style_tags(root: Path, floor: "tuple[int, int, int]") -> "dict[str, tuple]":
    """新しい形の候補のタグ: vX.Y.Z の形・版が floor 以上・注釈つき。 {tag: (版, object, commit)}"""
    rc, out = git(root, "for-each-ref", "refs/tags",
                  "--format=%(refname:short) %(objecttype) %(objectname) %(*objectname)")
    found = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        name, typ, obj = parts[0], parts[1], parts[2]
        ver = parse_tag(name)
        if ver is None or ver < floor or typ != "tag" or len(parts) < 4:
            continue
        found[name] = (ver, obj, parts[3])
    return found


def check_drift(root: Path, floor: "tuple[int, int, int]", releases: "list[dict]") -> int:
    """新しい形の Release ごとに、 記録したタグと今のタグを照らす。 照らした数を返す。"""
    n = 0
    for rel in releases:
        tag = str(rel.get("tag_name") or rel.get("tagName") or "")
        ver = parse_tag(tag)
        if ver is None or ver < floor:
            continue  # LEGACY（形の違うタグ・前の番号）は照らさない
        body = str(rel.get("body") or "")
        obj_m, com_m = NOTE_TAG_OBJECT.search(body), NOTE_COMMIT.search(body)
        if not obj_m or not com_m:
            raise Fail(f"{tag}: Release のノートに tag object か commit の記録が無い")
        info = tag_info(root, tag)
        if info is None:
            raise Fail(f"{tag}: Release はあるが、 タグが無い（消された）")
        typ, obj, commit = info
        if typ != "tag" or obj != obj_m.group(1) or commit != com_m.group(1):
            raise Fail(f"{tag}: タグが、 Release に記録した tag object か commit と違う（動いた）")
        n += 1
    return n


def previous_release(root: Path, floor, commit: str, tag: str,
                     releases: "list[dict] | None", main_ref: str) -> "tuple[str, str] | None":
    with_release = None
    if releases is not None:
        with_release = {str(r.get("tag_name") or r.get("tagName") or "") for r in releases}
    best = None
    for name, (ver, _obj, c) in new_style_tags(root, floor).items():
        if name == tag or c == commit:
            continue
        if with_release is not None and name not in with_release:
            continue
        if not is_ancestor(root, c, main_ref) or not is_ancestor(root, c, commit):
            continue
        if best is None or ver > best[0]:
            best = (ver, name, c)
    return (best[1], best[2]) if best else None


def verify_source(root: Path, commit: str, source: str, want: "tuple[int, int, int]") -> str:
    v = declared_version(root, commit, source)
    if not v:
        raise Fail("版の出どころが読めない（無いか、 形が崩れている）")
    if parse_release(v) is None:
        raise Fail("版の出どころが Release の版（X.Y.Z）ではない（開発の版にはタグを打たない）")
    if v != ".".join(map(str, want)):  # 文字の完全な一致（形は上で決まっている）
        raise Fail(f"版の出どころ（{safe_version(v)}）とタグの版（{'.'.join(map(str, want))}）が違う")
    return v


def safe_version(v: str) -> str:
    """誤りの文に出してよい形の版だけを出す（版の欄に何が書かれていても、 そのまま出さない）。"""
    return v if re.fullmatch(r"[0-9A-Za-z.+-]{1,40}", v) else "<形の崩れた版>"


def notes(product: str, version: str, tag: str, obj: str, commit: str,
          prev: "tuple[str, str] | None", ci_run_url: str) -> str:
    """識別だけのノート（commit の件名は載せない）。"""
    lines = [
        "## Product Release",
        "",
        f"- product: {product}",
        f"- version: {version}",
        f"- tag: {tag}",
        f"- tag object: {obj}",
        f"- commit: {commit}",
        f"- previous Product Release: {prev[0]}（commit {prev[1]}）" if prev
        else "- previous Product Release: なし（最初の新しい形の Product Release）",
        f"- CI run: {ci_run_url or '（記録なし）'}",
        "- artifact: なし（成果物の形と置き場は P1-03 で決める）",
        "- production: Deployment Gate v2 ができるまで、本番へ配置しない",
        "",
    ]
    return "\n".join(lines)


def cmd_check(a) -> int:
    root = Path(a.repo_root)
    floor = parse_release(a.first_version)
    want = parse_tag(a.tag)
    if want is None:
        raise Fail(f"タグの形が vX.Y.Z ではない（{shown(a.tag)}）")
    if want < floor:
        raise Fail(f"{a.tag}: --first-version より前の版（LEGACY の番号の範囲）")
    info = tag_info(root, a.tag)
    if info is None:
        raise Fail(f"{a.tag}: タグが無い")
    typ, obj, commit = info
    if typ != "tag":
        raise Fail(f"{a.tag}: 注釈つきのタグではない")
    main = resolve(root, a.main_ref)
    if not main:
        raise Fail("main の参照が読めない")
    if not is_ancestor(root, commit, main):
        raise Fail(f"{a.tag}: タグの commit が main の祖先ではない")
    version = verify_source(root, commit, a.version_source, want)
    releases = load_releases(a.releases_json)
    drifted = 0 if releases is None else check_drift(root, floor, releases)
    prev = previous_release(root, floor, commit, a.tag, releases, main)
    product = a.product or root.resolve().name
    out = {"product": product, "version": version, "tag": a.tag, "tag_object": obj,
           "commit": commit, "previous_tag": prev[0] if prev else "",
           "previous_commit": prev[1] if prev else "", "drift_checked": str(drifted)}
    for k, v in out.items():
        print(f"{k}={v}")
    if a.github_output:
        with open(a.github_output, "a", encoding="utf-8") as f:
            for k, v in out.items():
                f.write(f"{k}={v}\n")
    if a.notes_out:
        Path(a.notes_out).write_text(notes(product, version, a.tag, obj, commit, prev,
                                           a.ci_run_url or ""), encoding="utf-8")
    print("OK: Product Release の識別を満たす")
    return 0


def cmd_drift(a) -> int:
    root = Path(a.repo_root)
    n = check_drift(root, parse_release(a.first_version), load_releases(a.releases_json) or [])
    print(f"OK: 新しい形の Release {n} 件のタグは、記録したとおり")
    return 0


def cmd_preflight(a) -> int:
    root = Path(a.repo_root)
    floor = parse_release(a.first_version)
    want = parse_tag(a.tag)
    if want is None:
        raise Fail(f"タグの形が vX.Y.Z ではない（{shown(a.tag)}）")
    if want < floor:
        raise Fail(f"{a.tag}: --first-version より前の版（LEGACY の番号の範囲）")
    if tag_info(root, a.tag) is not None:
        raise Fail(f"{a.tag}: タグがもうある（作り直さない・動かさない）")
    if a.remote:
        r = subprocess.run(["git", "-C", str(root), "ls-remote", "--tags", a.remote, f"refs/tags/{a.tag}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise Fail("remote のタグを確かめられない")
        if r.stdout.strip():
            raise Fail(f"{a.tag}: remote にタグがもうある（作り直さない・動かさない）")
    if not SHA_RE.fullmatch(a.commit):
        raise Fail("--commit は 40 桁の SHA で渡す")
    commit = resolve(root, a.commit)
    if commit != a.commit:
        raise Fail("--commit の commit が手元に無い")
    main = resolve(root, a.main_ref)
    if not main or not is_ancestor(root, commit, main):
        raise Fail("commit が main の上に無い")
    version = verify_source(root, commit, a.version_source, want)
    releases = load_releases(a.releases_json)
    drifted = 0 if releases is None else check_drift(root, floor, releases)
    prev = previous_release(root, floor, commit, a.tag, releases, main)
    print(f"version={version}")
    print(f"tag={a.tag}")
    print(f"commit={commit}")
    print(f"previous_tag={prev[0] if prev else ''}")
    print(f"drift_checked={drifted}")
    print("OK: タグを打つ前の確かめを満たす（タグは Core の maintainer（人）が打つ）")
    return 0


def main(argv: "list[str] | None" = None) -> int:
    p = argparse.ArgumentParser(description="Product Release の識別を確かめる")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, need_tag=True):
        sp.add_argument("--repo-root", default=".")
        sp.add_argument("--first-version", required=True)
        sp.add_argument("--releases-json")
        if need_tag:
            sp.add_argument("--tag", required=True)
            sp.add_argument("--version-source", required=True, choices=("pyproject", "VERSION"))
            sp.add_argument("--main-ref", default="origin/main")

    c = sub.add_parser("check")
    common(c)
    c.add_argument("--product")
    c.add_argument("--ci-run-url")
    c.add_argument("--notes-out")
    c.add_argument("--github-output")
    d = sub.add_parser("drift")
    common(d, need_tag=False)
    f = sub.add_parser("preflight")
    common(f)
    f.add_argument("--commit", required=True)
    f.add_argument("--remote", help="remote にも同じタグが無いことを確かめる（例: origin）")
    a = p.parse_args(argv)
    if parse_release(a.first_version) is None:
        print("使い方の誤り: --first-version は X.Y.Z で渡す", file=sys.stderr)
        return 2
    try:
        return {"check": cmd_check, "drift": cmd_drift, "preflight": cmd_preflight}[a.cmd](a)
    except Fail as e:
        print(f"NG: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as e:
        print(f"NG: 読めない入力（{type(e).__name__}）", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

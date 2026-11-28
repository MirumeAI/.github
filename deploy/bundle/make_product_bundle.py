#!/usr/bin/env python3
"""製品のパッケージの束を作る（開発の機体で流す。 機体では流さない）。

案件に依らない共通の道具（正本は MirumeAI/.github の同じ道。 案件の Repository には写しを置く。 MirumeAI/.github#237）。

    python3 deploy/bundle/make_product_bundle.py [--out <束の置き場>] [--build-wheels <組み立ての道具の置き場>]
        [--python python3.10]

この Repository の commit（HEAD）の追跡しているファイルだけを一時の場所に写し（commit の木と blob をそのまま。 手元の
属性・filter・置き換えの ref を使わない）、 そこで製品の wheel を作って、 次の並びの束にする（PDM の Product Release の
束と同じ並び）:

    <束の置き場>/<製品の名前>/<製品の版>/
        wheelhouse/<製品の wheel>
        SHA256SUMS      束の中のファイルの sha256（道は束の根から）
        release.json    名前・版・commit・wheel・wheel_sha256・sha256sums_sha256

束の置き場の既定は、 案件のフォルダ（この Repository の 1 つ上）の `wheelhouse/`（Git の外）。

- 製品の名前は根の pyproject.toml の [project].name（入れる道具の read_declaration で読む。 標準の部品だけの読み手）、
  版は `src/<Python の名前>/__init__.py` の `__version__`（Python の名前は名前の `-` を `_` にした物）
- 製品の extra（部品の依存）の wheel は入れない。 判定の venv では、 同じ版が PDM の束（mirumeai-pdm[web] の依存）から入る
  （extra は判定の venv の lock の版に限る）。 入れる命令（install_venvs.py）は、 入れた後に extra の依存が満たされることを
  照らす
- 作業ツリーに commit していない変更があれば作らない（配る wheel は commit の中身だけから作る）
- 写すのは commit の object から順にたどった木と blob そのままで、 commit・木・blob の object の中身の hash を名前と照らす。 この道具が流す git には、
  `GIT_` で始まる名前を外し、 全体と利用者の設定を読まず、 置き換えの ref を使わない環境を渡す（手元の git の状態と環境で、
  commit と違う中身を写さないように）
- HEAD が origin/main に入っていなければ作らない（main に入った commit から作る。 `git fetch` の後に流す）
- 同じ版の束がもうあれば作らない（作り直しで中身が変わらないように。 版を上げてから作る）
- 作った束のファイルとフォルダから、 書き込みの権限を外す
- 終わりに、 System Release の記録の `product_package` に写す 3 つ（name・version・wheel_sha256）を出す

wheel は、 index を使わずに作る。 組み立ての道具（根の pyproject.toml の [build-system].requires。 どれも版を固定した
`名前==版`）は、 根の pyproject.toml の [tool.mirumeai.build-wheels]（wheel のファイルの名前 → sha256。 組み立ての道具の
依存の wheel も書く）に書いた wheel だけを、 組み立ての道具の置き場（既定は束の置き場の `build-wheels/`。 人が置く）から
写して sha256 を照らし、 使い捨ての venv に hash で入れる（`pip check` で依存も照らす）。 その venv で、 build isolation を
使わずに作る。 宣言は写した commit の木の物を読む。 組み立てには、 pip の設定のファイルと、 setuptools・distutils の利用者の
設定を読ませない（環境は残す名前を並べる形〔PATH・LANG・LC_ など〕。 HOME を空の一時の場所にし、 利用者の site を読まず、
pip に設定のファイルと index を使わせない）。 venv の種と同じ版の道具も、 照らした wheel から入れ直す。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


class Stop(Exception):
    """止める理由（人に見せる文）。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_env() -> "dict[str, str]":
    """この道具が流す git に渡す環境（GIT_ で始まる名前を外し、 全体と利用者の設定を読まず、 置き換えの ref と grafts を使わない）。
    Repository の中の設定（.git/config）は読むが、 写す中身は filter も属性も使わない読み方（copy_tree）で取る。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    return env


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env())
    if r.returncode != 0:
        raise Stop(f"git {' '.join(args[:2])} が落ちました")
    return r.stdout


VERSION = re.compile(r"[0-9][0-9A-Za-z.+_-]*")
#: 組み立ての道具の要求（[build-system].requires）の形: 版を固定した `名前==版` だけ（extra・印・範囲を書かない）
BUILD_PIN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)==([0-9][0-9A-Za-z.+!]*)")
#: 組み立ての道具の置き場の既定の名前（束の置き場の中）
BUILD_WHEELS = "build-wheels"
#: 組み立てに渡す環境で残す名前（ほかは LC_ で始まる名前だけ。 build_env）
BUILD_ENV_KEEP = ("PATH", "LANG", "LANGUAGE", "TZ", "TMPDIR", "SOURCE_DATE_EPOCH")
_IV = None


def install_tool():
    """隣の入れる道具（宣言の読み方 read_declaration を使う。 名前を指して読み、 流した所の sys.path に頼らない）。"""
    global _IV
    if _IV is None:
        spec = importlib.util.spec_from_file_location("_make_product_bundle_install_venvs", HERE / "install_venvs.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _IV = mod
    return _IV


def declared(repo: Path) -> "tuple[str, str]":
    """製品の配布物の名前（根の pyproject.toml の [project].name）と版（パッケージの __version__）。 標準の部品だけで読む。"""
    iv = install_tool()
    try:
        # 名前は束の道（<置き場>/<名前>/<版>）と版のファイルの道に使うので、 読み方が字を狭めて / と .. を断る
        declaration = iv.read_declaration(repo)
    except iv.Stop as e:
        raise Stop(str(e)) from None
    name, package = declaration["name"], declaration["package"]
    init = repo / "src" / package / "__init__.py"
    if init.is_symlink() or not init.is_file():
        raise Stop(f"src/{package}/__init__.py がありません")
    version = re.search(r'(?m)^__version__ = "([^"]+)"$', init.read_text(encoding="utf-8"))
    if version is None:
        raise Stop(f"src/{package}/__init__.py に __version__ がありません")
    if not VERSION.fullmatch(version.group(1)):
        raise Stop("製品の版に、使えない字があります（数字で始まり、英数字と .+_- だけ）")
    return name, version.group(1)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build_plan(repo: Path) -> "dict[str, tuple[str, str, str]]":
    """組み立ての道具の wheel（ファイルの名前 → (名前, 版, sha256)）。 根の pyproject.toml の [build-system].requires は、 どれも
    版を固定した `名前==版` で、 [tool.mirumeai.build-wheels] に、 その名前と版の wheel が 1 つずつある。 ほかに書いた
    wheel（組み立ての道具の依存）も、 名前ごとに 1 つだけ。"""
    iv = install_tool()
    try:
        declaration = iv.read_declaration(repo)
    except iv.Stop as e:
        raise Stop(str(e)) from None
    pins: "dict[str, str]" = {}
    for req in declaration["build_requires"]:
        m = BUILD_PIN.fullmatch(req)
        if m is None or normalize(m.group(1)) in pins:
            raise Stop(f"pyproject.toml の [build-system].requires の {req!r} は、版を固定した `名前==版` ではありません"
                       "（1 つの名前を 1 度だけ。組み立ての道具は hash で照らした wheel から入れる）")
        pins[normalize(m.group(1))] = m.group(2)
    if not pins:
        raise Stop("pyproject.toml の [build-system].requires がありません（組み立ての道具の版を固定して書く）")
    wheels: "dict[str, tuple[str, str, str]]" = {}
    seen: "dict[str, str]" = {}
    for file, digest in sorted(declaration["build_wheels"].items()):
        m = iv.WHEEL_FILE.fullmatch(file)
        dist, version = normalize(m.group(1)), m.group(2)
        if dist in seen:
            raise Stop(f"[tool.mirumeai.build-wheels] に、{dist} の wheel が 2 つあります")
        seen[dist] = version
        wheels[file] = (dist, version, digest)
    missing = [f"{n}=={v}" for n, v in sorted(pins.items()) if seen.get(n) != v]
    if missing:
        raise Stop(f"[tool.mirumeai.build-wheels] に、組み立ての道具 {', '.join(missing)} の wheel と sha256 がありません"
                   "（組み立ての道具の依存の wheel も書く）")
    return wheels


def copy_build_wheels(store: Path, wheels: "dict[str, tuple[str, str, str]]", dest: Path) -> None:
    """組み立ての道具の置き場から、 宣言した wheel だけを dest に写し、 写した物の sha256 を宣言と照らす（置き場のほかの物は
    使わない）。"""
    if store.is_symlink() or not store.is_dir():
        raise Stop(f"組み立ての道具の置き場がありません: {store}（[tool.mirumeai.build-wheels] の wheel を置く）")
    dest.mkdir()
    for file, (_, _, digest) in sorted(wheels.items()):
        source = store / file
        if source.is_symlink() or not source.is_file():
            raise Stop(f"組み立ての道具の置き場に {file} がありません（リンクは使わない）")
        target = dest / file
        shutil.copyfile(source, target)
        if sha256_file(target) != digest:
            raise Stop(f"組み立ての道具の {file} の sha256 が、[tool.mirumeai.build-wheels] と違います")


def build_env(home: Path) -> "dict[str, str]":
    """組み立てに渡す環境。 残す名前を並べる形（BUILD_ENV_KEEP と LC_ で始まる名前だけを残す。 PEP 517 の backend の道・
    setuptools と distutils の利用者の設定・Python の部品の道などの名前を、 並べ漏らしても通さない）。 HOME は空の一時の
    場所にし、 利用者の site を読まず、 pip には設定のファイルを読ませず、 index を使わせない。"""
    env = {k: v for k, v in os.environ.items() if k in BUILD_ENV_KEEP or k.startswith("LC_")}
    env.update(HOME=str(home), PYTHONNOUSERSITE="1", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1", PIP_NO_INDEX="1")
    return env


def write_sums(bundle: Path) -> str:
    """束の中のファイル（SHA256SUMS と release.json を除く）の sha256 を書き、 SHA256SUMS の sha256 を返す。"""
    lines = []
    for path in sorted(p for p in bundle.rglob("*") if p.is_file()):
        rel = path.relative_to(bundle).as_posix()
        if rel in ("SHA256SUMS", "release.json"):
            continue
        lines.append(f"{sha256_file(path)}  {rel}")
    (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sha256_file(bundle / "SHA256SUMS")


def read_only(top: Path) -> None:
    for path in sorted(top.rglob("*"), reverse=True) + [top]:
        mode = path.stat().st_mode
        path.chmod(mode & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


#: 写すふつうのファイルの mode（木の object の字）と、 写したファイルに付ける権限
FILE_MODES = {b"100644": 0o644, b"100664": 0o644, b"100755": 0o755}
TREE_MODE = b"40000"


def copy_tree(repo: Path, commit: str, dest: Path, run=subprocess.run, top: "tuple[str, ...] | None" = None) -> int:
    """commit の object から木を順にたどり、 blob を filter も属性も置き換えの ref も使わずに dest へ写して、 写したファイルの
    数を返す。 commit・木・blob の object は、 どれも中身の hash が名前と同じことを照らす（部分木の差し替えも
    止める）。 ふつうのファイル（100644・100755。 古い 100664 は 100644 と同じに扱う）の blob だけを受け、 リンク・submodule・
    外を指す名前（空・`.`・`..`・`/` を含む）・`.git` という名前（大文字と小文字を問わない。 git と同じ）・同じ木の中の同じ名前は、
    木を読む所で止める（何も書く前に）。 top を渡すと、 一番上の名前がその中にある物だけを写す（ほかは名前を読む前に
    バイトで照らして飛ばす。 一式の道具の機体に置く物の並びと、 宣言を先に読む pyproject.toml）。"""
    env = git_env()

    def git_run(*args: str, data: "bytes | None" = None):
        return run(["git", "-C", str(repo), "--no-replace-objects", *args], input=data, capture_output=True, env=env)
    fmt = git_run("rev-parse", "--show-object-format")
    algo = fmt.stdout.decode("ascii", "replace").strip() if fmt.returncode == 0 else "sha1"
    if algo not in ("sha1", "sha256"):
        raise Stop(f"git の object の形が分かりません: {algo[:20]}")
    width = 20 if algo == "sha1" else 32
    got = git_run("rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}")
    start = got.stdout.decode("ascii", "replace").strip()
    if got.returncode != 0 or not re.fullmatch(f"[0-9a-f]{{{width * 2}}}", start):
        raise Stop(f"commit を読めません: {commit[:40]}")

    def read(items: "list[tuple[str, str]]", kind: str) -> "list[bytes]":
        """(object の名前, 人に見せる名前) の object を読み、 型と、 中身の hash が名前と同じことを照らす。"""
        if not items:
            return []
        batch = git_run("cat-file", "--batch", data="".join(sha + "\n" for sha, _ in items).encode("ascii"))
        if batch.returncode != 0:
            raise Stop("git cat-file が落ちました")
        out, pos, bodies = batch.stdout, 0, []
        for sha, label in items:
            end = out.find(b"\n", pos)
            header = out[pos:end].decode("ascii", "replace").split() if end >= 0 else []
            if len(header) != 3 or header[:2] != [sha, kind] or not header[2].isdigit():
                raise Stop(f"{kind} を読めません: {label}")
            size = int(header[2])
            body = out[end + 1:end + 1 + size]
            pos = end + 1 + size + 1
            h = hashlib.new(algo)
            h.update(b"%s %d\0" % (kind.encode("ascii"), len(body)))
            h.update(body)
            if len(body) != size or h.hexdigest() != sha:
                raise Stop(f"{kind} の中身が名前の hash と違います: {label}")
            bodies.append(body)
        return bodies

    head = read([(start, start[:12])], "commit")[0]
    first = head.split(b"\n", 1)[0].split(b" ")
    if len(first) != 2 or first[0] != b"tree" or not re.fullmatch(rb"[0-9a-f]{%d}" % (width * 2), first[1]):
        raise Stop(f"commit の木を読めません: {start[:12]}")
    top_names = None if top is None else {t.encode("utf-8") for t in top}
    files: "list[tuple[str, str, int]]" = []
    level = [("", first[1].decode("ascii"))]
    while level:
        nxt = []
        for (prefix, _), body in zip(level, read([(sha, prefix or "/") for prefix, sha in level], "tree")):
            seen, i = set(), 0
            while i < len(body):
                space = body.find(b" ", i)
                nul = body.find(b"\0", space + 1) if space >= 0 else -1
                if space < 0 or nul < 0 or nul + 1 + width > len(body):
                    raise Stop(f"commit の木を読めません: {prefix or '/'}")
                mode, raw, child = body[i:space], body[space + 1:nul], body[nul + 1:nul + 1 + width].hex()
                i = nul + 1 + width
                if not prefix and top_names is not None and raw not in top_names:
                    continue
                if raw in seen:
                    raise Stop(f"commit の木に、同じ名前が 2 度あります: {prefix}{raw.decode('utf-8', 'replace')}")
                seen.add(raw)
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    raise Stop(f"commit の木に、読めない名前があります: {prefix}") from None
                path = prefix + name
                if name in ("", ".", "..") or "/" in name or "\0" in name or name.lower() == ".git":
                    raise Stop(f"commit に、リンクか外を指す道があります: {path}")
                if mode == TREE_MODE:
                    nxt.append((path + "/", child))
                elif mode in FILE_MODES:
                    files.append((path, child, FILE_MODES[mode]))
                else:
                    raise Stop(f"commit に、リンクか外を指す道かふつうのファイルでない物（mode {mode.decode('ascii', 'replace')}）があります: "
                               f"{path}")
        level = nxt
    for (path, _, perm), body in zip(files, read([(sha, path) for path, sha, _ in files], "blob")):
        target = dest.joinpath(*path.split("/"))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as f:
                f.write(body)
        except (FileExistsError, NotADirectoryError):
            raise Stop(f"commit の木に、同じ名前が 2 度あります: {path}") from None
        target.chmod(perm)
    return len(files)


def wheel_from_tree(src: Path, python: str, out: Path, tools: Path, wheels: "dict[str, tuple[str, str, str]]",
                    run=subprocess.run) -> Path:
    """写した木 src（copy_tree で commit から写した物）から製品の wheel を作り、 out に置く。 組み立ての道具は、 tools（照らした
    wheel だけを写した所）から使い捨ての venv に hash で入れ、 index と build isolation を使わない。"""
    with tempfile.TemporaryDirectory(prefix="product-wheel-") as tmp:
        tmp = Path(tmp)
        home = tmp / "home"
        home.mkdir()
        env = build_env(home)

        def step(cmd: "list[str]", what: str) -> None:
            r = run(cmd, capture_output=True, text=True, env=env)
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-3:]
                raise Stop(f"{what}（{' / '.join(tail)}）")

        venv = tmp / "build-venv"
        step([python, "-I", "-m", "venv", str(venv)], "組み立ての venv を作れません")
        vpy = str(venv / "bin" / "python")
        req = tmp / "build-requirements.txt"
        req.write_text("".join(f"{dist}=={version} --hash=sha256:{digest}\n"
                               for dist, version, digest in sorted(wheels.values())), encoding="utf-8")
        # venv の種（Python に付いてくる pip・setuptools）と同じ版でも、 照らした wheel から入れ直す（--force-reinstall）
        step([vpy, "-I", "-m", "pip", "install", "--no-index", "--no-deps", "--require-hashes", "--only-binary", ":all:",
              "--force-reinstall", "--find-links", str(tools), "-r", str(req)], "組み立ての道具を入れられません")
        step([vpy, "-I", "-m", "pip", "check"],
             "組み立ての道具の依存が足りないか合いません（[tool.mirumeai.build-wheels] に依存の wheel も書く）")
        made_dir = tmp / "wheels"
        step([vpy, "-I", "-m", "pip", "wheel", "--no-deps", "--no-index", "--no-build-isolation", "--check-build-dependencies",
              "--wheel-dir", str(made_dir), str(src)], "wheel を作れません")
        made = sorted(made_dir.glob("*.whl"))
        if len(made) != 1:
            raise Stop(f"wheel が 1 つではありません（{len(made)}）")
        out.mkdir(parents=True)
        dest = out / made[0].name
        shutil.copy2(made[0], dest)
        return dest


def make(repo: Path, out_root: Path, python: str, run=subprocess.run, build_store: "Path | None" = None) -> dict:
    here = declared(repo)
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise Stop("追跡しているファイルに commit していない変更があります（配る wheel は commit の中身だけから作る）")
    commit = git(repo, "rev-parse", "HEAD").strip()
    # main に入った commit からだけ作る（merge の前の Branch の commit から作ると、同じ版を作り直さない決まりで版の番号を
    # 使い切り、束の commit も main に無い物を指す。一式の道具と同じ照らし）。 追跡の ref を名指しする（`origin/main`
    # という名前のローカルの Branch やタグを先に読ませない）
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"],
                      capture_output=True, env=git_env()).returncode != 0:
        raise Stop(f"commit {commit[:12]} が origin/main に入っていません（main に入った commit から作る。 git fetch の後に"
                   "流す）")
    with tempfile.TemporaryDirectory(prefix="product-bundle-") as tmp:
        src = Path(tmp) / "src"
        src.mkdir()
        copy_tree(repo, commit, src, run=run)
        # 束の名前と版は、 写した commit の木から読む。 作業ツリーと違えば止める
        name, version = declared(src)
        if (name, version) != here:
            raise Stop(f"作業ツリーと commit で、製品の名前か版が違います（commit は {name} {version}）")
        if normalize(name) == BUILD_WHEELS:
            raise Stop(f"製品の名前が、組み立ての道具の置き場の名前（{BUILD_WHEELS}）と同じです")
        # 組み立ての道具も、 写した commit の木の宣言で照らす
        wheels = build_plan(src)
        tools = Path(tmp) / "build-wheels"
        copy_build_wheels(build_store if build_store is not None else out_root / BUILD_WHEELS, wheels, tools)
        bundle = out_root / name / version
        if bundle.exists():
            raise Stop(f"同じ版の束がもうあります: {name}/{version}（版を上げてから作る）")
        bundle.mkdir(parents=True)
        try:
            wheel = wheel_from_tree(src, python, bundle / "wheelhouse", tools, wheels, run=run)
        except BaseException:
            shutil.rmtree(bundle, ignore_errors=True)
            raise
    try:
        parts = wheel.name[:-4].split("-")
        if re.sub(r"[-_.]+", "-", parts[0]).lower() != re.sub(r"[-_.]+", "-", name).lower() or parts[1] != version:
            raise Stop("作った wheel の名前・版が、宣言と違います")
        sums_sha = write_sums(bundle)
        release = {"schema_version": 1, "product": name, "version": version, "commit": commit,
                   "wheel": wheel.name, "wheel_sha256": sha256_file(wheel), "sha256sums_sha256": sums_sha,
                   "files": sum(1 for p in bundle.rglob("*") if p.is_file()) + 1}
        (bundle / "release.json").write_text(json.dumps(release, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        read_only(bundle)
        return dict(release, bundle=str(bundle))
    except BaseException:
        shutil.rmtree(bundle, ignore_errors=True)
        raise


def main(argv: "list[str] | None" = None, repo: Path = REPO) -> int:
    ap = argparse.ArgumentParser(description="製品のパッケージの束を作る")
    ap.add_argument("--out", help="束の置き場（既定は案件のフォルダの wheelhouse/）")
    ap.add_argument("--build-wheels", help="組み立ての道具の置き場（既定は束の置き場の build-wheels/）")
    ap.add_argument("--python", default=shutil.which("python3.10") or sys.executable, help="wheel を作る Python")
    opts = ap.parse_args(argv)
    try:
        out_root = Path(opts.out).resolve() if opts.out else repo.parent / "wheelhouse"
        if out_root == repo or repo in out_root.parents:
            raise Stop("束の置き場が、この Repository の中です（Git の外に置く）")
        # 置き場のリンクの照らし（copy_build_wheels）が効くように、 名指しした道は resolve しない
        store = Path(opts.build_wheels).absolute() if opts.build_wheels else None
        if store is not None and (store.resolve() == repo.resolve() or repo.resolve() in store.resolve().parents):
            raise Stop("組み立ての道具の置き場が、この Repository の中です（Git の外に置く）")
        release = make(repo, out_root, opts.python, build_store=store)
        print(f"[product-bundle] 作りました: {release['product']} {release['version']}（commit {release['commit'][:12]}）")
        print("[product-bundle] System Release の記録の product_package に写す値:")
        print(f"  name: {release['product']}")
        print(f'  version: "{release["version"]}"')
        print(f'  wheel_sha256: "{release["wheel_sha256"]}"')
        return 0
    except Stop as e:
        print(f"[product-bundle] 止めます: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

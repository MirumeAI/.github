#!/usr/bin/env python3
"""新しい形（wheel を hash で入れた venv・起動の命令 serve）を、オフラインの機体へ USB で運ぶ一式を作り、機体で照らす。

案件に依らない共通の道具（正本は MirumeAI/.github の同じ道。案件の Repository には写しを置く。MirumeAI/.github#237）。
一式は、機体の新しい案件のフォルダとしてそのまま置く形。標準の部品だけを使う。運び方の決まりの正本は
MirumeAI/development-docs の `03_customer_development/core_verification_status.md`。
**`python3 -I -B` で流す**（付けなければ、ほかの部品を読む前に止める。-I は道具の隣の dir と PYTHON で始まる名前を読まず、
-B は __pycache__ を書かない。一式の中で流すとき、照らす前に一式の中のほかの物を読まず、一式に書かないため）。

作る（開発の機体で、この Repository の作業ツリーで流す。機体では流さない）:

    python3 -I -B deploy/bundle/install_set.py make --out <新しい dir> --label <一式の名前>
        [--commit <main に入った commit>] [--wheelhouse <束の置き場>] [--test]

照らす（機体で、一式の根で、system の python3 で。入れる前に。入れた後に照らし直すときは --installed）:

    python3 -I -B <Repository の名前>/deploy/bundle/install_set.py verify . --sums-sha256 <配置の計画に書いた 64 桁>

一式の並び（<一式> を、機体の新しい案件のフォルダとして置く）:
  <一式>/<Repository の名前>/  この Repository の commit の木（commit の木と blob をそのまま写す。手元の属性・filter・置き換えの
                               ref を使わない。.git は入れない）。根の物は、機体に置く物の並びだけ: 既定の並び TREE_TOP
                               （顧客案件の雛形の根の物のうち、開発の機体だけの物を除いた物。正本の試験が雛形と照らす）の
                               うち木にある物と、案件が根の pyproject.toml の [tool.mirumeai].install-set-extra（根の物の
                               名前の並び）に書いて足した物（tree_list）
  <一式>/wheelhouse/           ia/<タグ>/・pdm/<タグ>/（versions.yaml が選んだ両 Core の Product Release の束）・
                               <製品の名前>/<製品の版>/（製品のパッケージの束。木の commit と製品のファイルが同じ物）・
                               camera/（人が束の置き場の camera/ に置いたカメラの部品の wheel。置いたときだけ）。
                               束は、束の SHA256SUMS に書いた物と SHA256SUMS・release.json だけを写す
  <一式>/install-set.json      版の印（一式の名前・作った時刻・試しか・木の commit・両 Core と製品の版と sha256・カメラの wheel）
  <一式>/SHA256SUMS            一式の全部のファイル（SHA256SUMS 自身を除く）の sha256。この sha256 を配置の計画と配置の記録に
                               書き、配置の持ち主の例外で名指しする

機体では、verify の後に、入れる道具（install_venvs.py。--root に一式の根）で .venvs/ を作る。入れる道具は一式の根に
.venvs/ と .cache/（入れたときの記録）を作る。

make の照らし（どれか 1 つでも外れたら作らない。途中の物は消す）:
  - 作る先がまだ無く、Git の作業ツリーの外で、束の置き場の外。commit が origin/main に入っている。一式を作る道具
    （この道具・入れる道具・製品の束の道具）が、木の commit の物とバイトで同じ（--test のときはどちらも照らさず、版の印に
    試しと書く。試しの一式は、機体の verify が断る）。一式の名前が英数字と `._-` だけ
  - 根の pyproject.toml（木の commit の物）を読める。install-set-extra の物が、どれも commit の根にあり、根の 1 つの名前で、
    既定の並びと重ならず、開発の機体だけの物（DEV_ONLY と、versions.yaml のほかの `versions.` で始まる物）・`.git` でない。
    木にリンク・外を指す道・特別なファイルが無い
  - 製品の束の release.json の名前と版が木の宣言と同じで、束の commit と木の commit で製品の wheel の入力（PRODUCT_INPUTS）が
    同じ（違えば、版を上げて製品の束を作り直す）
  - 束に、リンク・特別なファイル・束の SHA256SUMS に無いファイルが無い。一式の中に、USB の FAT 系で重なる名前（大文字と
    小文字だけが違う）・使えない名前（`:*?"<>|`・制御の字・`\\`・先頭の空白・末尾の空白と `.`）が無い
  - 作った一式に、木の中の入れる道具の入れる前の照らし（install_venvs.py の plan。両 Core の束と versions.yaml・製品の束・
    撮像側の束が前提にした PDM・lock・カメラの wheel・製品の宣言の照らし）を流して通る
  データ・判定のモデル・ライセンス・秘密は入れない（木はこの Repository の commit で、それらを置かない決まり。束は配布物）。

verify の照らし（外れたら止める）。標準の部品だけで閉じ、一式の中のほかのコードを読まない:
  - SHA256SUMS の sha256 が --sums-sha256 と同じ。書いたファイルがすべてあり、ふつうのファイルで、sha256 が同じ
  - 一式の木と wheelhouse/ の中に、SHA256SUMS に無いファイルとリンクが無い（中の空の dir は見ない。中身を運べないため）。
    根に、SHA256SUMS に無い物（ファイル・dir・リンク）が無い（--installed のときだけ、根のほかの dir〔入れた後にできる
    .venvs/・.cache/、人が置いた作業ルートなど〕を見ずに名前を出す）
  - 試しの一式でない（開発の機体の通しでだけ --allow-test）
"""
import sys

if __name__ == "__main__" and not (sys.flags.isolated and sys.flags.dont_write_bytecode):
    # ほかの部品を読む前に止める（一式の中の道具の隣に足された物を読まず、一式に __pycache__ を書かないため）
    sys.stderr.write("[set] 止めます: python3 -I -B で流してください"
                     "（例: python3 -I -B <木>/deploy/bundle/install_set.py verify . --sums-sha256 <64 桁>）\n")
    sys.exit(2)

import argparse  # noqa: E402
import datetime  # noqa: E402
import hashlib  # noqa: E402
import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

#: 機体に置く物の既定の並び（根の物）。顧客案件の雛形から作った Repository の根の物のうち、開発の機体だけの物（DEV_ONLY）を
#: 除いた物（正本の試験が、雛形から作った最初の中身の根と照らす）。木に無い物は入れない。案件で足す根の物は、根の
#: pyproject.toml の [tool.mirumeai].install-set-extra に書く（tree_list）。並びは、この 1 つの代入の字のままにする
TREE_TOP = (".github", ".gitignore", "CLAUDE.md", "README.md", "acceptance", "config", "customer-profile.yaml", "deploy",
            "deployment", "evaluation", "machines", "pyproject.toml", "recipes", "release", "requirements", "scripts", "src",
            "tests", "validation", "versions.yaml")
#: 開発の機体だけの根の物（一式に入れない）。開発の固定の版のファイル（versions.yaml のほかの `versions.` で始まる物）も
#: 入れない（PINS_PREFIX）
DEV_ONLY = (".claude", "dev")
PINS_PREFIX = "versions."
#: install-set-extra に書ける根の物の名前（根の 1 つの名前。道・`.`・`..` は書けない）
ROOT_NAME = re.compile(r"[A-Za-z0-9._-]+")
#: 一式を作る道具（試しでない一式では、木の commit の物とバイトで同じことを照らす）
TOOLS = ("install_set.py", "install_venvs.py", "make_product_bundle.py")
#: 製品の wheel の入力になりうる物（束の commit と木の commit で同じことを照らす。git の pathspec）
PRODUCT_INPUTS = ("pyproject.toml", "src", "setup.py", "setup.cfg", "MANIFEST.in", ":(glob)**/.gitattributes",
                  ":(glob)LICEN[CS]E*", ":(glob)LICENCE*", ":(glob)NOTICE*", ":(glob)AUTHORS*", ":(glob)COPYING*")
MARK = "install-set.json"
SUMS = "SHA256SUMS"
RELEASE = "release.json"
SCHEMA = 1
#: 入れる道具が一式の根に作る dir（verify --installed は見ずに名前を出す）
AFTER_INSTALL = (".venvs", ".cache")
LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
REPO_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
FAT_BAD = re.compile(r'[:*?"<>|\\\x00-\x1f\x7f]')
FAT_LIMIT = 4 * 1024 ** 3


class Stop(Exception):
    """止める理由（人に見せる文）。"""


def say(text: str) -> None:
    print(f"[set] {text}", flush=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside(path: Path, top: Path) -> bool:
    try:
        path.relative_to(top)
        return True
    except ValueError:
        return False


def read_sums(top: Path, label: str) -> "dict[str, str]":
    """SHA256SUMS（道 → sha256）。外を指す道・重なり・形の違う行を断る。"""
    path = top / SUMS
    if path.is_symlink() or not path.is_file():
        raise Stop(f"{label}に {SUMS} がありません")
    out: "dict[str, str]" = {}
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        m = re.fullmatch(r"([0-9a-f]{64})  (\S.*)", line)
        if not m:
            raise Stop(f"{label}の {SUMS} の {n} 行目の形が違います")
        rel = m.group(2)
        if rel.startswith("/") or ".." in Path(rel).parts or rel in out:
            raise Stop(f"{label}の {SUMS} の {n} 行目の道が外を指すか重なっています")
        out[rel] = m.group(1)
    return out


def read_json(path: Path, label: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise Stop(f"{label} を読めません") from None
    if not isinstance(data, dict):
        raise Stop(f"{label} の形が違います")
    return data


def all_paths(top: Path) -> "list[Path]":
    out = []
    for here, dirs, files in os.walk(top):
        out += [Path(here) / n for n in sorted(dirs) + sorted(files)]
    return sorted(out)


# ---------------------------------------------------------------- 作る（隣の道具は make のときだけ読む）

def _load(name: str, base: Path):
    sys.dont_write_bytecode = True          # 読んだ道具の __pycache__ を書かない（作る一式の中を読むときも）
    spec = importlib.util.spec_from_file_location(f"_install_set_{name}_{abs(hash(str(base)))}", base / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git_env() -> "dict[str, str]":
    """この道具が流す git に渡す環境（製品の束を作る道具の git_env と同じ形。GIT_ で始まる名前を外し、全体と利用者の設定を
    読まず、置き換えの ref と grafts を使わない）。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1", GIT_GRAFT_FILE=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    return env


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=git_env())
    if r.returncode != 0:
        raise Stop(f"git {args[0]} が落ちました（{(r.stderr or '').strip().splitlines()[-1:]}）")
    return r.stdout


def git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, env=git_env()).returncode == 0


def repo_name(repo: Path) -> str:
    """一式の中の木の名前。origin の URL の最後の名前（作業ツリーの dir の名前にしない）。"""
    url = git(repo, "remote", "get-url", "origin").strip()
    name = re.split(r"[/:]", url.rstrip("/"))[-1]
    name = name[:-4] if name.endswith(".git") else name
    if not REPO_NAME.fullmatch(name):
        raise Stop("origin の URL から Repository の名前を取れません")
    return name


def inside_work_tree(path: Path) -> bool:
    """path が Git の作業ツリーの中か。git が「Git の Repository でない」のほかの訳で止まったとき（持ち主の違う Repository の
    dubious ownership など）は、中として扱う（作る先の照らしを、止める向きに倒す）。git の文は英語で読む。"""
    r = subprocess.run(["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"], capture_output=True, text=True,
                       env=dict(git_env(), LC_ALL="C", LANGUAGE="C"))
    if r.returncode == 0:
        return r.stdout.strip() == "true"
    return "not a git repository" not in (r.stderr or "")


def tree_list(declaration: dict) -> "tuple[str, ...]":
    """機体に置く根の物の並び: 既定の並び TREE_TOP と、案件が根の pyproject.toml の [tool.mirumeai].install-set-extra に書いて
    足した物（入れる道具の read_declaration が読んだ物）。足す物は根の 1 つの名前で、既定の並びと重ならず、開発の機体だけの
    物（DEV_ONLY と、`versions.` で始まるほかの物）と .git でない。"""
    extra = declaration["install_set_extra"]
    for name in extra:
        if not ROOT_NAME.fullmatch(name) or name in (".", ".."):
            raise Stop(f"install-set-extra の {name!r} は、根の 1 つの名前ではありません（道を書かない）")
        if name.casefold() == ".git":
            raise Stop("install-set-extra に .git は書けません")
        if name in TREE_TOP:
            raise Stop(f"install-set-extra の {name} は、既定の並びにあります（書かない）")
        if name in DEV_ONLY or name.startswith(PINS_PREFIX):
            raise Stop(f"install-set-extra の {name} は、開発の機体だけの物です（一式に入れない）")
    if len(set(extra)) != len(extra):
        raise Stop("install-set-extra に、同じ名前が 2 度あります")
    return TREE_TOP + tuple(extra)


def extract_tree(repo: Path, commit: str, dest: Path, pb, iv, scratch: Path) -> "tuple[dict, list[str]]":
    """commit の木の根の pyproject.toml を読んで機体に置く物の並び（tree_list）を決め、commit の木のうちその物だけを dest に
    出す。製品の束を作る道具の copy_tree で、commit の木と blob をそのまま写す（git archive は手元の置き換えの ref・属性の
    export-ignore と filter・GIT_CONFIG_COUNT などに従い、commit と違う中身を写しうる）。宣言は、作業ツリーの物でなく
    commit の物を先に写して読む。(宣言, 既定の並びのうち木に無い物) を返す。"""
    head = scratch / "declaration"
    head.mkdir()
    try:
        pb.copy_tree(repo, commit, head, top=("pyproject.toml",))
    except pb.Stop as e:
        raise Stop(str(e)) from None
    try:
        declaration = iv.read_declaration(head)
    except iv.Stop as e:
        raise Stop(str(e)) from None
    top = tree_list(declaration)
    roots = set(git(repo, "ls-tree", "--name-only", "-z", commit).split("\0")) - {""}
    missing = [e for e in declaration["install_set_extra"] if e not in roots]
    if missing:
        raise Stop(f"commit の根に、install-set-extra に書いた物がありません: {', '.join(missing)}")
    dest.mkdir()
    try:
        pb.copy_tree(repo, commit, dest, top=top)
    except pb.Stop as e:
        raise Stop(str(e)) from None
    return declaration, [e for e in TREE_TOP if e not in roots]


def copy_bundle(src: Path, dst: Path, label: str) -> None:
    """束を写す。束の SHA256SUMS に書いた物と SHA256SUMS・release.json だけ（ほかの物・リンク・特別なファイルは断る）。"""
    if src.is_symlink() or not src.is_dir():
        raise Stop(f"{label}がありません（束の置き場を確かめる）")
    allowed = set(read_sums(src, label)) | {SUMS, RELEASE}
    dst.mkdir(parents=True)
    for here, dirs, files in os.walk(src):
        base = Path(here)
        for d in sorted(dirs):
            if (base / d).is_symlink():
                raise Stop(f"{label}にリンクがあります: {(base / d).relative_to(src)}")
            (dst / (base / d).relative_to(src)).mkdir()
        for f in sorted(files):
            p = base / f
            rel = p.relative_to(src).as_posix()
            if p.is_symlink() or not p.is_file():
                raise Stop(f"{label}に、リンクかふつうでないファイルがあります: {rel}")
            if rel not in allowed:
                raise Stop(f"{label}に、束の {SUMS} に無いファイルがあります: {rel}（束の外の物は写さない）")
            shutil.copyfile(p, dst / rel)


def check_names(top: Path) -> None:
    """USB の FAT 系で重なる名前・使えない名前と、SHA256SUMS の行を崩す名前を断る。"""
    seen: "dict[str, str]" = {}
    for p in all_paths(top):
        rel = p.relative_to(top).as_posix()
        name = p.name
        if FAT_BAD.search(rel) or name != name.strip() or name.endswith("."):
            raise Stop(f"一式に、USB の FAT 系で使えない名前があります: {rel!r}")
        key = rel.casefold()
        if key in seen:
            raise Stop(f"一式に、大文字と小文字だけが違う名前があります（USB の FAT 系で重なる）: {seen[key]} と {rel}")
        seen[key] = rel


def write_sums(top: Path) -> str:
    lines = []
    for p in all_paths(top):
        rel = p.relative_to(top).as_posix()
        if p.is_symlink():
            raise Stop(f"一式にリンクがあります: {rel}")
        if p.is_file() and rel != SUMS:
            lines.append(f"{sha256_file(p)}  {rel}")
    (top / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sha256_file(top / SUMS)


def product_bundle(repo: Path, pb, iv, tree: Path, commit: str, wheelhouse: Path) -> "tuple[str, str, Path, dict]":
    """木の宣言（名前・版）の製品の束と、その release.json。束の commit と木の commit で製品の wheel の入力が同じことを照らす。"""
    try:
        name, version = pb.declared(tree)
    except pb.Stop as e:
        raise Stop(str(e)) from None
    bundle = wheelhouse / iv.normalize(name) / version
    if bundle.is_symlink() or not (bundle / RELEASE).is_file():
        raise Stop(f"製品の束がありません: {iv.normalize(name)}/{version}（make_product_bundle.py で作る）")
    release = read_json(bundle / RELEASE, f"製品の束 {version} の {RELEASE}")
    if iv.normalize(str(release.get("product", ""))) != iv.normalize(name) or str(release.get("version")) != version:
        raise Stop(f"製品の束 {version} の {RELEASE} の名前・版が、木の宣言と違います")
    made_from = str(release.get("commit", ""))
    if not HEX40.fullmatch(made_from) or not git_ok(repo, "cat-file", "-e", f"{made_from}^{{commit}}"):
        raise Stop(f"製品の束 {version} を作った commit が、この Repository にありません")
    if not git_ok(repo, "diff", "--quiet", made_from, commit, "--", *PRODUCT_INPUTS):
        raise Stop(f"製品の束 {version}（commit {made_from[:12]}）と木の commit {commit[:12]} で、製品の wheel の入力"
                   "（pyproject.toml・src/ など）が違います（版を上げて、木の commit で製品の束を作り直す）")
    return name, version, bundle, release


def precheck(tiv, tree: Path, wheelhouse: Path, tmp: Path, name: str, version: str, wheel_sha256: str) -> dict:
    """木の中の入れる道具の入れる前の照らし（plan）を、作った一式に流す。機体の設定と System Release は照らしのための仮の物。"""
    machine = tmp / "SETCHECK.yaml"
    machine.write_text("machine:\n  symbol: SETCHECK\n  kind: installed\n", encoding="utf-8")
    release = tmp / "release.yaml"
    release.write_text(f'product_package:\n  name: {name}\n  version: "{version}"\n'
                       f'  wheel_sha256: "{wheel_sha256}"\n', encoding="utf-8")
    try:
        return tiv.plan(tree, wheelhouse, machine, release)
    except tiv.Stop as e:
        raise Stop(str(e)) from None


def make(repo: Path, out: Path, label: str, rev: str, wheelhouse: Path, test: bool) -> dict:
    if not LABEL.fullmatch(label):
        raise Stop("一式の名前は、英数字で始まり英数字と ._- だけ（64 字まで）にします")
    if out.exists() or out.is_symlink():
        raise Stop(f"作る先がもうあります: {out}")
    if not out.parent.is_dir():
        raise Stop(f"作る先の親の dir がありません: {out.parent}")
    if inside_work_tree(out.parent):
        raise Stop("作る先が Git の作業ツリーの中です（外に作る）")
    if _inside(out, wheelhouse.resolve()):
        raise Stop("作る先が束の置き場の中です（外に作る）")
    partial = out.with_name(out.name + ".partial")
    if partial.exists() or partial.is_symlink():
        raise Stop(f"前の途中の物があります: {partial}（中を見てから消す）")
    commit = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").strip()
    # 追跡の ref を名指しする（`origin/main` という名前のローカルの Branch やタグを先に読ませない）
    if not test and not git_ok(repo, "merge-base", "--is-ancestor", commit, "refs/remotes/origin/main"):
        raise Stop(f"commit {commit[:12]} が origin/main に入っていません（main に入った commit から作る。"
                   "git fetch の後に流す。試しの一式は --test）")
    name_of_repo = repo_name(repo)
    iv, pb = _load("install_venvs", HERE), _load("make_product_bundle", HERE)
    partial.mkdir()
    try:
        with tempfile.TemporaryDirectory(prefix=".install-set-", dir=out.parent) as tmp_name:
            tmp = Path(tmp_name)
            tree = partial / name_of_repo
            _, absent = extract_tree(repo, commit, tree, pb, iv, tmp)
            if not test:
                differ = [t for t in TOOLS if not (tree / "deploy" / "bundle" / t).is_file()
                          or (tree / "deploy" / "bundle" / t).read_bytes() != (HERE / t).read_bytes()]
                if differ:
                    raise Stop(f"一式を作る道具が、木の commit の物と違います: {', '.join(differ)}（木の commit の道具で"
                               "流す。例: その commit の作業ツリーで流す。試しの一式は --test）")
            try:
                versions = iv.read_versions(tree)
                camera = iv.read_camera(wheelhouse)
            except iv.Stop as e:
                raise Stop(str(e)) from None
            wh = partial / "wheelhouse"
            cores = {}
            for key, short in iv.CORES.items():
                v = versions[key]
                copy_bundle(wheelhouse / short / v["tag"], wh / short / v["tag"], f"{short} {v['tag']} の束")
                cores[short] = {k: v[k] for k in ("tag", "commit", "sha256sums_sha256", "wheel_sha256")}
            name, version, bundle, release = product_bundle(repo, pb, iv, tree, commit, wheelhouse)
            copy_bundle(bundle, wh / iv.normalize(name) / version, f"製品の束 {version}")
            if camera:
                (wh / iv.CAMERA_DIR).mkdir()
                for c in camera:
                    shutil.copyfile(wheelhouse / iv.CAMERA_DIR / c["file"], wh / iv.CAMERA_DIR / c["file"])
                    if sha256_file(wh / iv.CAMERA_DIR / c["file"]) != c["sha256"]:
                        raise Stop(f"{iv.CAMERA_DIR}/ の {c['file']} が、照らした後に変わりました")
            tiv = _load("install_venvs", tree / "deploy" / "bundle")       # 機体で流す物と同じ道具で照らす
            precheck(tiv, tree, wh, tmp, name, version, str(release.get("wheel_sha256", "")))
        mark = {"schema": SCHEMA, "tool": "deploy/bundle/install_set.py", "label": label, "test": test,
                "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "repository": {"name": name_of_repo, "commit": commit},
                "cores": cores,
                "product": {"name": name, "version": version, "commit": release.get("commit"),
                            "wheel_sha256": release.get("wheel_sha256"),
                            "sha256sums_sha256": release.get("sha256sums_sha256")},
                "camera": camera}
        (partial / MARK).write_text(json.dumps(mark, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        check_names(partial)
        sums = write_sums(partial)
        files = [p for p in all_paths(partial) if p.is_file()]
        big = [p.relative_to(partial).as_posix() for p in files if p.stat().st_size >= FAT_LIMIT]
        size = sum(p.stat().st_size for p in files)
        partial.rename(out)
        return dict(mark, sums_sha256=sums, files=len(files), size=size, big=big, absent=absent)
    except BaseException:
        shutil.rmtree(partial, ignore_errors=True)
        raise


# ---------------------------------------------------------------- 照らす（標準の部品だけ。一式の中のほかのコードを読まない）

def verify(top: Path, want: str, installed: bool = False, allow_test: bool = False) -> dict:
    """一式を SHA256SUMS と照らし、版の印と、見なかった根の dir の名前を返す。"""
    if not HEX64.fullmatch(want or ""):
        raise Stop("--sums-sha256 は 64 桁の sha256 にします（配置の計画に書いた値）")
    top = Path(os.path.abspath(top))
    if top.is_symlink() or not top.is_dir():
        raise Stop("一式の根が dir ではないか、リンクです")
    sums_path = top / SUMS
    if sums_path.is_symlink() or not sums_path.is_file():
        raise Stop(f"一式に {SUMS} がありません")
    if sha256_file(sums_path) != want:
        raise Stop(f"{SUMS} の sha256 が、渡した値と違います（配置の計画の値と照らす。一式を作り直したか、写し違い）")
    real = top.resolve()
    entries = read_sums(top, "一式")
    for rel, digest in entries.items():
        p = top / rel
        parts = Path(rel).parts
        if any(top.joinpath(*parts[:i]).is_symlink() for i in range(1, len(parts) + 1)) or not p.is_file() \
                or not _inside(p.resolve(), real):
            raise Stop(f"{SUMS} に書いたファイルが無いか、リンクです: {rel}")
        if sha256_file(p) != digest:
            raise Stop(f"ファイルが {SUMS} と違います: {rel}")
    if MARK not in entries:
        raise Stop(f"一式に版の印（{MARK}）がありません")
    ours = {rel.split("/", 1)[0] for rel in entries if "/" in rel}
    skipped = []
    for child in sorted(top.iterdir()):
        if child.name == SUMS:
            continue
        if child.is_symlink():
            raise Stop(f"一式の根にリンクがあります: {child.name}")
        if child.name in ours and child.is_dir():
            for p in all_paths(child):
                rel = p.relative_to(top).as_posix()
                if p.is_symlink() or (not p.is_dir() and rel not in entries):
                    raise Stop(f"一式に、{SUMS} に無いファイルかリンクがあります: {rel}")
        elif child.name in entries and child.is_file():
            continue
        elif installed and child.is_dir():
            skipped.append(child.name)
        else:
            raise Stop(f"一式の根に、{SUMS} に無い物があります: {child.name}（入れる前の一式には、一式の物だけがある。"
                       "入れた後に照らし直すときは --installed）")
    mark = read_json(top / MARK, f"版の印（{MARK}）")
    if mark.get("test") and not allow_test:
        raise Stop("試しの一式です（--test で作った物。機体へ入れない。開発の機体の通しでだけ --allow-test）")
    return dict(mark, skipped=skipped, files=len(entries))


# ---------------------------------------------------------------- 入口

def main(argv: "list[str] | None" = None, repo: Path = REPO) -> int:
    ap = argparse.ArgumentParser(description="新しい形を機体へ USB で運ぶ一式を作る・照らす（python3 -I -B で流す）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make", help="一式を作る（開発の機体で）")
    mk.add_argument("--out", required=True, help="作る一式の dir（まだ無い所。Git の作業ツリーと束の置き場の外）")
    mk.add_argument("--label", required=True, help="一式の名前（版の印に書く。英数字と ._-）")
    mk.add_argument("--commit", default="origin/main", help="木の commit（既定は origin/main。main に入った物）")
    mk.add_argument("--wheelhouse", help="束の置き場（既定は案件のフォルダの wheelhouse/）")
    mk.add_argument("--test", action="store_true", help="試しの一式（main に入っていない commit と、木の commit と違う道具も"
                                                       "受け、版の印に試しと書く。機体の verify が断る）")
    vf = sub.add_parser("verify", help="一式を照らす（機体で、入れる前に）")
    vf.add_argument("set", help="一式の根")
    vf.add_argument("--sums-sha256", required=True, help="配置の計画に書いた、一式の SHA256SUMS の sha256")
    vf.add_argument("--installed", action="store_true", help="入れた後に照らし直す（根のほかの dir を見ずに名前を出す）")
    vf.add_argument("--allow-test", action="store_true", help="試しの一式も受ける（開発の機体の通しでだけ）")
    opts = ap.parse_args(argv)
    try:
        if opts.cmd == "make":
            out = Path(opts.out).resolve()
            wheelhouse = Path(opts.wheelhouse).resolve() if opts.wheelhouse else repo.parent / "wheelhouse"
            r = make(repo, out, opts.label, opts.commit, wheelhouse, opts.test)
            say(f"作りました: {out}（一式 {r['label']}・木 {r['repository']['name']} {r['repository']['commit'][:12]}・"
                f"PDM {r['cores']['pdm']['tag']}・撮像側 {r['cores']['ia']['tag']}・製品 {r['product']['version']}）")
            say("木の中の入れる道具の入れる前の照らし（束と versions.yaml・製品の束・lock・カメラの wheel）は通りました")
            if r["absent"]:
                say(f"機体に置く物の既定の並びのうち、木に無い物（入れていない）: {', '.join(r['absent'])}")
            if r["test"]:
                say("試しの一式です（機体へ持っていかない。機体の verify は断る）")
            for c in r["camera"]:
                say(f"カメラの部品: {c['name']} {c['version']}・sha256 {c['sha256']}（配置の計画に前もって書き、照らす）")
            if not r["camera"]:
                say("カメラの部品: なし（束の置き場の camera/ に wheel がありません）")
            say(f"ファイル {r['files']}・{r['size'] / 1024 ** 3:.2f} GiB")
            for b in r["big"]:
                say(f"4 GiB 以上のファイル: {b}（FAT32 の USB には写せません。exFAT にする）")
            say(f"{SUMS} の sha256: {r['sums_sha256']}（配置の計画と配置の記録に書き、配置の持ち主の例外で名指しする）")
            say("System Release の記録の product_package に写す値:")
            say(f"  name: {r['product']['name']}")
            say(f'  version: "{r["product"]["version"]}"')
            say(f'  wheel_sha256: "{r["product"]["wheel_sha256"]}"')
            return 0
        r = verify(Path(opts.set), opts.sums_sha256, installed=opts.installed, allow_test=opts.allow_test)
        say(f"一式は {SUMS} のとおりです（ファイル {r['files']}）: 一式 {r.get('label')}・"
            f"木 {str((r.get('repository') or {}).get('commit', ''))[:12]}・"
            f"PDM {(r.get('cores') or {}).get('pdm', {}).get('tag')}・撮像側 {(r.get('cores') or {}).get('ia', {}).get('tag')}・"
            f"製品 {(r.get('product') or {}).get('version')}{'・試しの一式（機体へ入れない）' if r.get('test') else ''}")
        for c in r.get("camera") or []:
            say(f"カメラの部品: {c.get('name')} {c.get('version')}・sha256 {c.get('sha256')}（配置の計画の値と照らす）")
        for n in r["skipped"]:
            say(f"見なかった根の dir: {n}/（{'入れた後にできる物' if n in AFTER_INSTALL else '一式の外の物'}。中は照らしていない）")
        return 0
    except Stop as e:
        print(f"[set] 止めます: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

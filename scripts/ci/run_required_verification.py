#!/usr/bin/env python3
"""merge の前提の検査（Required Verification）を、 PR で動く workflow と同じ段で手元で流す。

2026-10-19 から GitHub Actions を使わない（Public のこの Repository だけは動く）。
merge の前提は、 **PR で動く workflow の段そのものを、 merge する head で手元で
流して PASS すること** になった。 ところが流し方は workflow の YAML にしか無く、
手で組み立てると段が抜け、 PyYAML が無いと試験が飛ばされたまま「OK」と出た。
これまでの検査は一時の script で組んでいて、 共有されないまま消えた。

    python3 <.github の clone>/scripts/ci/run_required_verification.py <検査する Repository の clone>
        [--head <commit>] [--base origin/main] [--python 3.10=<道>] [--list] [--keep]

すること:

  ・検査する Repository の `.github/workflows/` から、 `pull_request` で動く
    workflow を選ぶ（`branches`・`paths`・`paths-ignore`・`types` の条件も見る。
    変更のファイルは、 `--base` と head の merge-base からの差）
  ・job を `needs` の順に、 段を書いた順に流す。 段が落ちたら、 その job の
    残りは `if: always()` などの段だけを流す（CI と同じ）
  ・再利用 workflow（`MirumeAI/.github/...@main`）と正本の道具は、 この道具を
    置いた clone の `--canonical-ref`（既定 origin/main）から取る。
    `actions/checkout` は、 head か正本の新しい clone に置き換える
  ・**job ごとに、 新しい clone・workflow の Python の版の新しい venv・仮の HOME
    で流す。** 元の clone と HOME は変えない（pip と Playwright の cache だけ共有）。
    環境変数は、 決めた名前だけを渡す（token・ssh の agent・git の設定などは渡さない）。
    段が終わったら、 後ろで動き続ける子の process も止める
  ・**知らない書き方は、 流す前に止める。** 知らない action・式・値・matrix・
    defaults などを黙って飛ばすと、 段の抜けた「PASS」になる

結果は段ごとの終了コード・試験の件数・飛ばした試験の件数で、 PASS のときは
PR に書く記録の行を出す。 記録の行には、 head が基準を含むか・正本の ref・
流した道具の版（正本と同じか）も入れる。 終了コード: 0 PASS ／ 1 FAIL ／
2 流せない（書き方・Python の版・引数・対象の workflow が無い）。

**PR のコードを手元で動かす。** 中身を読んでから流す。 仮の HOME と環境変数の
取り除きは、 手元の設定や認証を**うっかり**使わせないためのもので、 同じ OS の
ユーザーで動くコードを閉じ込める箱ではない（本物の HOME の認証の file や手元の
データを読め、 network にも出せる）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None          # 無ければ、 道具用の venv を作って流し直す（_bootstrap_yaml）

HERE = Path(__file__).resolve().parent
TOOL_REL = "scripts/ci/run_required_verification.py"
CANONICAL_SLUG = "MirumeAI/.github"
EVENT = "pull_request"
RECORD_PASS = "GitHub Actions unavailable — canonical verification PASS"

#: 出力の行を崩しうる字の種類（制御・書式・行の区切り・段落の区切り。 U+0085 は Cc）。
#: 名前にあると、 出力に記録の行に似た独立した行を作れる・文字の向きを変えて見せかけを作れる（#77）
BREAKING = ("Cc", "Cf", "Zl", "Zp")


def _show(name) -> str:
    """出力に出す名前（workflow のファイル名・job・段・対象外の理由）。 行を崩しうる字は \\uXXXX にする。"""
    return "".join(f"\\u{ord(c):04x}" if unicodedata.category(c) in BREAKING else c for c in str(name))

#: 再利用 workflow は正本の main だけ。 ほかの Repository や枝を指すものは、
#: 手元で同じものを取れる保証が無いので止める。
REUSABLE = re.compile(r"^MirumeAI/\.github/(\.github/workflows/[^@/]+\.ya?ml)@main$")

#: 読める鍵。 **`defaults` は入れない**（段の shell や置き場所を変えるのに、
#: 読まずに流すと別の場所・別の shell で PASS になる）。
WORKFLOW_KEYS = {"name", "on", "permissions", "jobs", "env", "concurrency", "run-name"}
JOB_KEYS = {"name", "runs-on", "timeout-minutes", "steps", "needs", "if", "env",
            "permissions", "uses", "with", "concurrency"}
STEP_KEYS = {"name", "id", "uses", "with", "run", "env", "if", "shell",
             "working-directory", "timeout-minutes", "continue-on-error"}
PR_FILTER_KEYS = {"branches", "branches-ignore", "paths", "paths-ignore", "types"}

#: PR のコードへ渡す環境変数。 **ここに無いものは渡さない**（token・ssh の agent・
#: git の設定と認証・手元の画面へ届くものなど。 除く名前の表は、 抜けが出る）。
_PASS_ENV = {"PATH", "LANG", "LANGUAGE", "TERM", "USER", "LOGNAME", "SHELL", "TZ",
             "LD_LIBRARY_PATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH",
             "PIP_CACHE_DIR", "PLAYWRIGHT_BROWSERS_PATH"}
#: proxy は、 認証（`user:pass@`）を含まないときだけ渡す
_PROXY_ENV = {"http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"}


class Unsupported(Exception):
    """流せない書き方。 黙って飛ばさずに止める。"""


# ------------------------------------------------------------------ PyYAML
def _bootstrap_yaml() -> None:
    """PyYAML が無ければ、 道具用の venv を作って入れ、 その中で流し直す。

    **無いまま流すと、 workflow を読めない。** 手で入れる手順を増やさないよう、
    cache の下に道具専用の venv を作る（検査する job の venv とは別）。
    """
    if yaml is not None:
        return
    if os.environ.get("MIRUME_RV_BOOTSTRAPPED"):
        sys.exit("PyYAML を入れた venv でも読み込めませんでした。 道具用の venv を消して流し直してください")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    venv = cache / "mirumeai" / "required-verification-venv"
    py = venv / "bin" / "python"
    try:
        if not py.exists():
            print(f"PyYAML が無いので、 道具用の venv を作ります: {venv}", file=sys.stderr)
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
        if subprocess.run([str(py), "-c", "import yaml"], capture_output=True).returncode:
            subprocess.run([str(py), "-m", "pip", "install", "--quiet", "pyyaml"], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"道具用の venv を作れませんでした（{e}）。 Python の venv と pip が要ります", file=sys.stderr)
        sys.exit(2)
    env = dict(os.environ, MIRUME_RV_BOOTSTRAPPED="1")
    os.execve(str(py), [str(py), str(Path(__file__).resolve()), *sys.argv[1:]], env)


# ------------------------------------------------------------------ 式
_TOKEN = re.compile(r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+(?:\.\d+)?)"
                    r"|(?P<op>==|!=|&&|\|\||!|\(|\))|(?P<name>[A-Za-z_][A-Za-z0-9_.\-]*))")
_STATUS = ("success", "always", "failure", "cancelled")


def _tokens(text: str) -> list:
    out, i = [], 0
    while i < len(text):
        if text[i:].strip() == "":
            break
        m = _TOKEN.match(text, i)
        if not m or m.end() == i:
            raise Unsupported(f"式を読めません: {text!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        i = m.end()
    return out


def _truthy(v) -> bool:
    return not (v is None or v is False or v == "" or (isinstance(v, (int, float)) and not isinstance(v, bool) and v == 0))


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v.strip()) if v.strip() else 0.0
    except ValueError:
        return float("nan")


def _equal(a, b) -> bool:
    """GitHub の比べ方: 文字どうしは大文字小文字を区別しない。 型が違えば数にする。"""
    if isinstance(a, str) and isinstance(b, str):
        return a.lower() == b.lower()
    if type(a) is type(b):
        return a == b
    return _num(a) == _num(b)


class Expr:
    """workflow の式（`${{ }}` と `if:`）の、 今の workflow が使う範囲だけを読む。

    **知らない値・関数は止める**（Unsupported）。 黙って空にすると、 段の条件が
    変わったまま流れる。 status の関数は job の今の状態（success / failure /
    skipped）で決まる。 演算子の強さは GitHub と同じ（`!` > `==` `!=` > `&&` > `||`）。
    """

    def __init__(self, ctx: dict, status: str = "success"):
        self.ctx, self.status = ctx, status

    def eval(self, text: str):
        self._t, self._i = _tokens(text), 0
        v = self._or()
        if self._i != len(self._t):
            raise Unsupported(f"式を読めません: {text!r}")
        return v

    def uses_status(self, text: str) -> bool:
        return any(k == "name" and v in _STATUS for k, v in _tokens(text))

    def _peek(self):
        return self._t[self._i] if self._i < len(self._t) else (None, None)

    def _take(self, kind=None, value=None):
        k, v = self._peek()
        if k is None or (kind and k != kind) or (value and v != value):
            raise Unsupported("式の途中で読めなくなりました")
        self._i += 1
        return v

    def _or(self):
        v = self._and()
        while self._peek() == ("op", "||"):
            self._take()
            rhs = self._and()
            v = v if _truthy(v) else rhs
        return v

    def _and(self):
        v = self._cmp()
        while self._peek() == ("op", "&&"):
            self._take()
            rhs = self._cmp()
            v = rhs if _truthy(v) else v
        return v

    def _cmp(self):
        v = self._unary()
        if self._peek() in (("op", "=="), ("op", "!=")):
            op = self._take()
            rhs = self._unary()
            return _equal(v, rhs) if op == "==" else not _equal(v, rhs)
        return v

    def _unary(self):
        if self._peek() == ("op", "!"):
            self._take()
            return not _truthy(self._unary())
        return self._primary()

    def _primary(self):
        k, v = self._peek()
        if k == "op" and v == "(":
            self._take()
            inner = self._or()
            self._take("op", ")")
            return inner
        if k == "str":
            self._take()
            return v[1:-1].replace("''", "'")
        if k == "num":
            self._take()
            return float(v) if "." in v else int(v)
        if k == "name":
            self._take()
            if v in ("true", "false"):
                return v == "true"
            if v == "null":
                return None
            if v in _STATUS:
                self._take("op", "(")
                self._take("op", ")")
                return {"success": self.status == "success", "always": True,
                        "failure": self.status == "failure", "cancelled": False}[v]
            return self._lookup(v)
        raise Unsupported("式を読めません")

    def _lookup(self, name: str):
        parts = name.split(".")
        if parts[0] == "env":
            # env の文脈は workflow・job・段に書いたもの（と GITHUB_ENV）だけ。 手元の環境変数は入れない
            return self.ctx.get("env", {}).get(".".join(parts[1:]), "")
        cur = self.ctx
        for p in parts:
            if not isinstance(cur, dict) or p not in cur:
                raise Unsupported(f"知らない値: {name}")
            cur = cur[p]
        return cur


def _to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


_SUBST = re.compile(r"\$\{\{(.*?)\}\}", re.S)


def _subst(text: str, ex: Expr) -> str:
    return _SUBST.sub(lambda m: _to_str(ex.eval(m.group(1))), text)


def _cond(raw, ex: Expr) -> bool:
    """`if:` を評価する。 status の関数が無ければ `success() && (...)` と同じ。"""
    if raw is None:
        return ex.status == "success"
    if isinstance(raw, bool):
        return raw and ex.status == "success"
    text = str(raw).strip()
    m = re.fullmatch(r"\$\{\{(.*)\}\}", text, re.S)
    if m:
        text = m.group(1)
    v = _truthy(ex.eval(text))
    return v if ex.uses_status(text) else (v and ex.status == "success")


# ------------------------------------------------------------------ paths・branches
def _glob(pat: str) -> re.Pattern:
    """workflow の filter の glob。 `**` は `/` を越え、 `*`・`?` は越えない。"""
    if "+" in pat or "[" in pat:
        raise Unsupported(f"この glob は読めません: {pat!r}")
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(name: str, patterns: list) -> bool:
    """後ろの `!` の pattern が、 前の一致を打ち消す（GitHub と同じ順の決まり）。"""
    hit = False
    for p in patterns:
        if p.startswith("!"):
            if _glob(p[1:]).match(name):
                hit = False
        elif _glob(p).match(name):
            hit = True
    return hit


def _pr_filter(on) -> dict | None:
    """workflow が pull_request で動くなら、 その条件。 動かないなら None。

    `pull_request_target` は止める（base の側の workflow と権限で動くので、
    head の workflow を流しても同じにならない）。
    """
    if isinstance(on, str):
        events = {on}
    elif isinstance(on, (list, dict)):
        events = {str(e) for e in on}
    else:
        raise Unsupported(f"on: を読めません: {on!r}")
    if "pull_request_target" in events:
        raise Unsupported("pull_request_target の workflow は流せません")
    if EVENT not in events:
        return None
    cfg = (on.get(EVENT) if isinstance(on, dict) else None) or {}
    if not isinstance(cfg, dict) or set(cfg) - PR_FILTER_KEYS:
        raise Unsupported(f"pull_request の条件を読めません: {cfg!r}")
    for k, v in cfg.items():
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise Unsupported(f"pull_request の {k} は文字の並び（list）にしてください: {v!r}")
    return cfg


def _selected(cfg: dict, base_branch: str, changed: list) -> tuple:
    types = cfg.get("types")
    if types is not None and not {"opened", "synchronize", "reopened"} & set(types):
        return False, "PR の更新では動かない（types）"
    if "branches" in cfg and not _matches(base_branch, cfg["branches"]):
        return False, f"基準の枝 {base_branch} が branches に合わない"
    if "branches-ignore" in cfg and _matches(base_branch, cfg["branches-ignore"]):
        return False, f"基準の枝 {base_branch} が branches-ignore に合う"
    if "paths" in cfg and not any(_matches(f, cfg["paths"]) for f in changed):
        return False, "paths に合う変更が無い"
    if "paths-ignore" in cfg and changed and all(_matches(f, cfg["paths-ignore"]) for f in changed):
        return False, "変更がすべて paths-ignore に合う"
    return True, ""


# ------------------------------------------------------------------ git
def _git(repo: Path, *args, check=True) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and p.returncode:
        raise Unsupported(f"git {' '.join(args)} が失敗しました: {p.stderr.strip()}")
    return p.stdout.strip()


def _git_paths(repo: Path, cmd: str, *args, check=True) -> list:
    """git が出すファイルの名前の一覧。 **-z で読む**（#73）。

    既定（`core.quotepath=true`）では、 ASCII でない名前が `"docs/\\343\\203..."` の形（引用と
    8 進）で出て、 workflow の `paths` に合わず、 その workflow が黙って「対象外」になった。
    -z なら名前をそのまま NUL で区切って出す（改行を含む名前も 1 つのまま）。
    """
    p = subprocess.run(["git", "-C", str(repo), cmd, "-z", *args], capture_output=True)
    if check and p.returncode:
        err = p.stderr.decode("utf-8", "replace").strip()
        raise Unsupported(f"git {cmd} {' '.join(args)} が失敗しました: {err}")
    return [f for f in p.stdout.decode("utf-8", "surrogateescape").split("\0") if f]


def _slug(repo: Path) -> tuple:
    url = _git(repo, "remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return (f"{m.group(1)}/{m.group(2)}" if m else f"local/{repo.name}"), (url or None)


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _clone(src: Path, sha: str, dest: Path, origin_url: str | None) -> None:
    """head の新しい clone。 元の clone の作業ツリー・ref・設定は変えない。

    `--shared` で元の objects を読むだけにする（大きな履歴を写さない）。
    LFS は CI の checkout と同じく取り出さない（pointer のまま）。
    """
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    dest.parent.mkdir(parents=True, exist_ok=True)
    for args in (["clone", "--quiet", "--shared", "--no-checkout", str(src), str(dest)],
                 ["-C", str(dest), "checkout", "--quiet", "--detach", sha]):
        p = subprocess.run(["git", *args], capture_output=True, text=True, env=env)
        if p.returncode:
            raise RuntimeError(f"clone できませんでした: {p.stderr.strip()}")
    if origin_url:
        subprocess.run(["git", "-C", str(dest), "remote", "set-url", "origin", origin_url],
                       capture_output=True, check=True)


def _inside(base: Path, rel: str) -> Path:
    """作業場所の中の道。 **外へ出る道（絶対の道・`..`）は止める。**"""
    p = (base / rel).resolve()
    b = base.resolve()
    if p != b and b not in p.parents:
        raise RuntimeError(f"作業場所の外の道は使えません: {rel!r}")
    return p


def _plain_rel(value: str, where: str) -> None:
    if value.startswith("/") or ".." in Path(value).parts:
        raise Unsupported(f"{where}: 作業場所の外へ出る道は使えません: {value!r}")


# ------------------------------------------------------------------ 計画
class Plan:
    """流す workflow・job・段を、 流す前に全部読んで確かめたもの。"""

    def __init__(self):
        self.jobs: list = []
        self.skipped: list = []     # (workflow, 理由)
        self.pythons: set = set()


def _load_yaml(text: str, where: str) -> dict:
    d = yaml.safe_load(text)
    if not isinstance(d, dict):
        raise Unsupported(f"{where}: workflow を読めません")
    if True in d and "on" not in d:          # YAML 1.1 では on: が true になる
        d["on"] = d.pop(True)
    return d


def _check_keys(d: dict, allowed: set, where: str) -> None:
    unknown = set(d) - allowed
    if unknown:
        raise Unsupported(f"{where}: 知らない鍵 {sorted(unknown)}")


def _minutes(v, where: str) -> None:
    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0):
        raise Unsupported(f"{where}: timeout-minutes は正の数にしてください")


def _check_steps(steps, where: str, target_slug: str) -> None:
    if not isinstance(steps, list) or not steps:
        raise Unsupported(f"{where}: steps がありません")
    for i, s in enumerate(steps):
        w = f"{where} の段 {i + 1}"
        if not isinstance(s, dict):
            raise Unsupported(f"{w}: 段を読めません")
        _check_keys(s, STEP_KEYS, w)
        if ("run" in s) == ("uses" in s):
            raise Unsupported(f"{w}: run と uses のどちらか 1 つにしてください")
        if s.get("shell") not in (None, "bash", "sh"):
            raise Unsupported(f"{w}: shell {s.get('shell')!r} は流せません")
        _minutes(s.get("timeout-minutes"), w)
        if not isinstance(s.get("continue-on-error", False), bool):
            raise Unsupported(f"{w}: continue-on-error は true / false にしてください")
        uses = s.get("uses")
        if uses is None:
            continue
        action = str(uses).split("@")[0]
        with_ = s.get("with") or {}
        if action == "actions/checkout":
            repo = with_.get("repository")
            if set(with_) - {"path", "repository", "fetch-depth"} or repo not in (None, target_slug, CANONICAL_SLUG):
                raise Unsupported(f"{w}: この checkout は流せません（{dict(with_)}）")
        elif action == "actions/setup-python":
            if set(with_) - {"python-version", "cache"} or not with_.get("python-version"):
                raise Unsupported(f"{w}: この setup-python は流せません（{dict(with_)}）")
        else:
            raise Unsupported(f"{w}: action {uses} は流せません")


def _job_entry(wf: str, name: str, spec: dict, inputs: dict, needs: list, where: str,
               plan: Plan, target_slug: str) -> dict:
    _check_keys(spec, JOB_KEYS, where)
    runs_on = spec.get("runs-on")
    if not (isinstance(runs_on, str) and runs_on.startswith("ubuntu-")):
        raise Unsupported(f"{where}: runs-on {runs_on!r} は流せません（ubuntu だけ）")
    _minutes(spec.get("timeout-minutes"), where)
    _check_steps(spec.get("steps"), where, target_slug)
    # setup-python の版は inputs だけで決まる形にする（再利用 workflow は inputs.python-version）
    for s in spec["steps"]:
        if str(s.get("uses", "")).split("@")[0] == "actions/setup-python":
            v = _subst(str(s["with"]["python-version"]), Expr({"inputs": inputs, "env": {}}))
            if not re.fullmatch(r"\d+\.\d+", v):
                raise Unsupported(f"{where}: python-version {v!r} は 3.10 のような版にしてください")
            plan.pythons.add(v)
    return {"workflow": wf, "name": name, "spec": spec, "inputs": inputs, "needs": needs}


def _inputs(call_spec: dict, with_: dict) -> dict:
    """再利用 workflow の入力。 **式は評価しない**（呼び出し側の式を手元で正しく評価できる
    保証が無い。 読み流すと、 守りの段を開け閉めする入力が黙って変わる）。"""
    declared = call_spec.get("inputs") or {}
    unknown = set(with_) - set(declared)
    if unknown:
        raise Unsupported(f"再利用 workflow に無い入力: {sorted(unknown)}")
    out = {}
    for k, d in declared.items():
        d = d or {}
        if d.get("required") and k not in with_ and "default" not in d:
            raise Unsupported(f"再利用 workflow の入力 {k} が渡されていません")
        v = with_.get(k, d.get("default"))
        if isinstance(v, str) and "${{" in v:
            raise Unsupported(f"再利用 workflow の入力 {k} の式は流せません: {v!r}")
        typ = d.get("type", "string")
        if typ == "boolean":
            if isinstance(v, str) and v.strip().lower() in ("true", "false"):
                v = v.strip().lower() == "true"
            elif v is None:
                v = False
            elif not isinstance(v, bool):
                raise Unsupported(f"再利用 workflow の入力 {k} は true / false にしてください: {v!r}")
        elif typ == "number":
            if isinstance(v, bool) or not isinstance(v, (int, float, type(None))):
                raise Unsupported(f"再利用 workflow の入力 {k} は数にしてください: {v!r}")
        elif typ == "string":
            v = "" if v is None else _to_str(v)
        else:
            raise Unsupported(f"再利用 workflow の入力 {k} の型 {typ!r} は流せません")
        out[k] = v
    return out


def plan_for(target: Path, head: str, canonical: Path, canonical_sha: str, base_branch: str,
             changed: list, target_slug: str) -> Plan:
    plan = Plan()
    listing = _git_paths(target, "ls-tree", "--name-only", head, ".github/workflows/", check=False)
    files = sorted(f for f in listing if f.endswith((".yml", ".yaml")))
    # **名前のまま読むようになったので、 出力を崩す名前は流す前に止める**（#73）。 改行などの
    # 制御文字は「対象外」の行や見出しに混ざり、 記録の行に似た行を作れる。 UTF-8 でない名前
    # （surrogateescape の文字）は、 流した後に記録を書く所で落ちる。 前は黙って読まれなかった
    for f in files:
        if any(ord(c) < 0x20 or ord(c) == 0x7F or 0xDC80 <= ord(c) <= 0xDCFF for c in f):
            raise Unsupported(f"workflow のファイルの名前に、 制御文字か UTF-8 でない文字があります: {ascii(f)}")
    for f in files:
        wf = Path(f).name
        d = _load_yaml(_git(target, "show", f"{head}:{f}"), wf)
        cfg = _pr_filter(d.get("on"))
        if cfg is None:
            plan.skipped.append((wf, "pull_request で動かない"))
            continue
        ok, why = _selected(cfg, base_branch, changed)
        if not ok:
            plan.skipped.append((wf, why))
            continue
        _check_keys(d, WORKFLOW_KEYS, wf)
        for jn, spec in (d.get("jobs") or {}).items():
            if not isinstance(spec, dict):
                raise Unsupported(f"{wf} の job {jn} を読めません")
            needs = spec.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            if "uses" in spec:
                m = REUSABLE.match(str(spec["uses"]))
                if not m:
                    raise Unsupported(f"{wf} の job {jn}: 再利用 workflow {spec.get('uses')} は流せません")
                _check_keys(spec, {"uses", "with", "needs", "if", "permissions", "name"}, f"{wf} の job {jn}")
                rw = _load_yaml(_git(canonical, "show", f"{canonical_sha}:{m.group(1)}"), m.group(1))
                _check_keys(rw, WORKFLOW_KEYS, m.group(1))
                on = rw.get("on")
                if not (isinstance(on, dict) and "workflow_call" in on):
                    raise Unsupported(f"{m.group(1)} は workflow_call で動きません")
                inputs = _inputs(on["workflow_call"] or {}, spec.get("with") or {})
                for ij, ispec in (rw.get("jobs") or {}).items():
                    if not isinstance(ispec, dict):
                        raise Unsupported(f"{m.group(1)} の job {ij} を読めません")
                    ineeds = ispec.get("needs") or []
                    ineeds = [ineeds] if isinstance(ineeds, str) else list(ineeds)
                    e = _job_entry(wf, f"{jn}/{ij}", ispec, inputs,
                                   needs + [f"{jn}/{x}" for x in ineeds],
                                   f"{wf} の job {jn}（{Path(m.group(1)).name} の {ij}）", plan, target_slug)
                    e["caller_if"] = spec.get("if")
                    e["workflow_env"] = rw.get("env") or {}
                    plan.jobs.append(e)
            else:
                e = _job_entry(wf, jn, spec, {}, needs, f"{wf} の job {jn}", plan, target_slug)
                e["caller_if"] = None
                e["workflow_env"] = d.get("env") or {}
                plan.jobs.append(e)
    # needs を名前で解く（再利用 workflow を呼ぶ job を needs に書いたら、 その中の job 全部）
    names = {(j["workflow"], j["name"]) for j in plan.jobs}
    for j in plan.jobs:
        resolved = []
        for n in j["needs"]:
            hits = [x for (w, x) in names if w == j["workflow"] and (x == n or x.startswith(n + "/"))]
            if not hits:
                raise Unsupported(f"{j['workflow']} の job {j['name']}: needs の {n} がありません")
            resolved += hits
        j["needs"] = resolved
    return plan


def _precheck(jobs: list, probe: dict) -> None:
    """式と道を、 流す前に全部読めるか確かめる（途中で止まると、 一部だけ流したことになる）。"""
    for j in jobs:
        where = f"{j['workflow']} の job {j['name']}"
        ex = Expr({**probe, "inputs": j["inputs"], "env": {}})
        for raw in (j["spec"].get("if"), j.get("caller_if")):
            if raw is not None and not isinstance(raw, bool):
                _cond(raw, ex)
        defined = {}
        for src in (j["workflow_env"], j["spec"].get("env") or {}):
            for k, v in src.items():
                defined[k] = _subst(str(v), Expr({**probe, "inputs": j["inputs"], "env": defined}))
        for i, s in enumerate(j["spec"]["steps"], 1):
            sex = Expr({**probe, "inputs": j["inputs"], "env": defined})
            senv = {k: _subst(str(v), sex) for k, v in (s.get("env") or {}).items()}
            sex = Expr({**probe, "inputs": j["inputs"], "env": {**defined, **senv}})
            if s.get("if") is not None and not isinstance(s.get("if"), bool):
                _cond(s["if"], sex)
            for v in (s.get("run"), *((s.get("with") or {}).values())):
                if isinstance(v, str):
                    _subst(v, sex)
            for key, v in (("working-directory", s.get("working-directory")),
                           ("path", (s.get("with") or {}).get("path"))):
                if isinstance(v, str):
                    _plain_rel(_subst(v, sex), f"{where} の段 {i} の {key}")


# ------------------------------------------------------------------ 流す
_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_SKIP = re.compile(r"^(?:OK|FAILED) \(.*?skipped=(\d+)", re.M)
#: guard の回帰テスト（scripts/ci/test_guard_main.sh）の数え方
_GUARD = re.compile(r"^合格 (\d+) / 不合格 (\d+)$", re.M)


def _counts(log: str) -> tuple:
    ran = sum(map(int, _RAN.findall(log))) + sum(int(a) + int(b) for a, b in _GUARD.findall(log))
    return ran, sum(map(int, _SKIP.findall(log)))


def _base_env(real_cache: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k in _PASS_ENV or k.startswith("LC_") or (k in _PROXY_ENV and "@" not in v)}
    env.setdefault("PIP_CACHE_DIR", str(real_cache / "pip"))
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(real_cache / "ms-playwright"))
    if not env.get("LANG", "").lower().endswith(("utf-8", "utf8")):
        env["LANG"] = "C.UTF-8"
    env.update({"GIT_TERMINAL_PROMPT": "0", "CI": "true", "GITHUB_ACTIONS": "true", "RUNNER_OS": "Linux"})
    return env


def _apply_github_files(env: dict, defined: dict, files: dict) -> None:
    """段が GITHUB_ENV・GITHUB_PATH に書いたものを、 次の段へ渡す（GitHub と同じく、
    GITHUB_ENV は env の文脈にも入り、 GITHUB_PATH は書いた順に PATH の先頭へ足す）。"""
    lines, i = files["GITHUB_ENV"].read_text(encoding="utf-8").splitlines(), 0
    while i < len(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)<<(.+)$", lines[i])
        if m:
            if m.group(2) not in lines[i + 1:]:
                raise RuntimeError("GITHUB_ENV の区切りが閉じていません")
            end = lines.index(m.group(2), i + 1)
            env[m.group(1)] = defined[m.group(1)] = "\n".join(lines[i + 1:end])
            i = end + 1
            continue
        if "=" in lines[i]:
            k, v = lines[i].split("=", 1)
            env[k] = defined[k] = v
        i += 1
    for p in files["GITHUB_PATH"].read_text(encoding="utf-8").splitlines():
        if p.strip():
            env["PATH"] = p.strip() + os.pathsep + env["PATH"]
    files["GITHUB_ENV"].write_text("")
    files["GITHUB_PATH"].write_text("")


def _kill_group(pgid: int) -> None:
    """段の process の組を止める。 **段が終わった後も、 後ろで動く子を残さない**
    （GitHub の runner も job の終わりに止める）。 setsid で抜けたものは止められない。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        end = time.monotonic() + 5
        while time.monotonic() < end:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)


def _run_job(job: dict, ctx_base: dict, a: argparse.Namespace, jobdir: Path, logdir: Path,
             pythons: dict, real_cache: Path) -> dict:
    spec = job["spec"]
    ws, home, temp = jobdir / "ws", jobdir / "home", jobdir / "temp"
    for d in (ws, home, temp):
        d.mkdir(parents=True)
    files = {k: temp / k.lower() for k in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY")}
    for p in files.values():
        p.write_text("")
    event = temp / "event.json"
    event.write_text(json.dumps({"pull_request": ctx_base["github"]["event"]["pull_request"]}))
    env = _base_env(real_cache)
    env.update({"HOME": str(home), "TMPDIR": str(temp), "RUNNER_TEMP": str(temp),
                "GITHUB_WORKSPACE": str(ws), "GITHUB_EVENT_NAME": EVENT,
                "GITHUB_EVENT_PATH": str(event), "GITHUB_REPOSITORY": ctx_base["github"]["repository"],
                "GITHUB_SHA": a.head_sha, "GITHUB_BASE_REF": ctx_base["github"]["base_ref"],
                "GITHUB_HEAD_REF": a.head_ref, **{k: str(v) for k, v in files.items()}})
    ctx = json.loads(json.dumps(ctx_base))
    ctx["github"]["workspace"] = str(ws)
    ctx["runner"]["temp"] = str(temp)
    ctx["inputs"] = job["inputs"]
    defined = {}                    # env の文脈（workflow・job・段に書いたもの）
    for src in (job["workflow_env"], spec.get("env") or {}):
        for k, v in src.items():
            defined[k] = _subst(str(v), Expr({**ctx, "env": defined}))
    env.update(defined)
    deadline = time.monotonic() + float(spec.get("timeout-minutes") or 360) * 60
    status, results = "success", []
    for i, step in enumerate(spec["steps"], 1):
        name = step.get("name") or step.get("uses") or str(step.get("run", "")).strip().splitlines()[0][:60]
        r = {"n": i, "name": name, "result": "skipped", "secs": 0.0, "ran": 0, "skipped_tests": 0}
        results.append(r)
        senv_def = {k: _subst(str(v), Expr({**ctx, "env": defined}, status))
                    for k, v in (step.get("env") or {}).items()}
        ex = Expr({**ctx, "env": {**defined, **senv_def}}, status)
        if not _cond(step.get("if"), ex):
            continue
        t0 = time.monotonic()
        log = logdir / f"{i:02d}.log"
        try:
            ok = _run_step(step, ex, {**env, **senv_def}, ws, temp, log, a, pythons, jobdir, env, deadline,
                           float(step["timeout-minutes"]) * 60 if step.get("timeout-minutes") else None)
        except RuntimeError as e:
            log.write_text(f"{e}\n", encoding="utf-8")
            ok = False
        r["secs"] = round(time.monotonic() - t0, 1)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        r["ran"], r["skipped_tests"] = _counts(text)
        if ok:
            r["result"] = "ok"
            _apply_github_files(env, defined, files)
        elif step.get("continue-on-error") is True:
            r["result"] = "failed（続けた）"
        else:
            r["result"] = "timeout" if time.monotonic() >= deadline or "時間の上限" in text else "failed"
            status = "failure"
    return {"status": status, "steps": results}


def _run_step(step, ex, senv, ws, temp, log, a, pythons, jobdir, env, deadline, step_timeout) -> bool:
    uses = step.get("uses")
    with_ = {k: _subst(str(v), ex) for k, v in (step.get("with") or {}).items()}
    if uses:
        action = uses.split("@")[0]
        if action == "actions/checkout":
            dest = _inside(ws, with_.get("path") or "")
            if with_.get("repository") == CANONICAL_SLUG:
                _clone(a.canonical, a.canonical_sha, dest, "https://github.com/MirumeAI/.github.git")
            else:
                _clone(a.target, a.head_sha, dest, a.origin_url)
            log.write_text(f"checkout → {dest}\n", encoding="utf-8")
            return True
        # actions/setup-python: workflow の版の新しい venv を作り、 以後の段の PATH の先頭に置く
        ver = with_["python-version"]
        venv = jobdir / f"venv-{ver}"
        p = subprocess.run([pythons[ver], "-m", "venv", str(venv)], capture_output=True, text=True)
        log.write_text(f"setup-python {ver}: {pythons[ver]} → {venv}\n{p.stdout}{p.stderr}", encoding="utf-8")
        if p.returncode:
            return False
        env["PATH"] = str(venv / "bin") + os.pathsep + env["PATH"]
        env["VIRTUAL_ENV"] = str(venv)
        env["pythonLocation"] = str(venv)
        return True
    script = temp / f"step-{log.stem}.sh"
    script.write_text(_subst(str(step["run"]), ex), encoding="utf-8")
    shell = step.get("shell")
    # GitHub と同じ: 書かないときは bash -e、 bash と書いたときは pipefail も
    cmd = (["sh", "-e", str(script)] if shell == "sh"
           else ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)] if shell == "bash"
           else ["bash", "-e", str(script)])
    cwd = _inside(ws, _subst(step["working-directory"], ex)) if step.get("working-directory") else ws
    remaining = deadline - time.monotonic()
    limit = min(remaining, step_timeout) if step_timeout else remaining
    if limit <= 0:
        log.write_text("job の時間の上限を過ぎました\n", encoding="utf-8")
        return False
    with open(log, "wb") as fh:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=senv, stdout=fh, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            rc = proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            _kill_group(proc.pid)
            proc.wait()
            fh.write(f"\n時間の上限（{limit:.0f} 秒）を過ぎたので止めました\n".encode())
            return False
        except KeyboardInterrupt:
            _kill_group(proc.pid)
            raise
        finally:
            _kill_group(proc.pid)
    return rc == 0


def _python_map(wanted: set, given: list) -> dict:
    out = {}
    for g in given:
        if "=" not in g:
            raise Unsupported(f"--python は 版=道 の形で渡してください: {g}")
        v, p = g.split("=", 1)
        out[v] = p
    for v in sorted(wanted):
        p = out.get(v) or shutil.which(f"python{v}")
        if not p:
            raise Unsupported(f"Python {v} が見つかりません。 python{v} を PATH に置くか、 --python {v}=<道> で渡してください")
        got = subprocess.run([p, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                             capture_output=True, text=True).stdout.strip()
        if got != v:
            raise Unsupported(f"{p} は Python {got or '?'} で、 {v} ではありません")
        out[v] = p
    return out


def _order(jobs: list) -> list:
    done, out, pending = set(), [], list(jobs)
    while pending:
        progressed = False
        for j in list(pending):
            if all((j["workflow"], n) in done for n in j["needs"]):
                out.append(j)
                done.add((j["workflow"], j["name"]))
                pending.remove(j)
                progressed = True
        if not progressed:
            raise Unsupported("needs が輪になっています")
    return out


def _rmtree(path: Path) -> None:
    def force(func, p, _exc):
        if not os.path.islink(p):
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=force)


def _head_ref(target: Path, head: str, head_sha: str) -> str:
    """GITHUB_HEAD_REF に入れる、 PR の元の枝の名前。 分からなければ空でない仮の名前。"""
    names = [head] if head != "HEAD" else []
    names.append("HEAD")
    for name in names:
        full = _git(target, "rev-parse", "--symbolic-full-name", name, check=False)
        if full.startswith("refs/heads/"):
            return full[len("refs/heads/"):]
        if full.startswith("refs/remotes/") and full.count("/") >= 3:
            return full.split("/", 3)[3]
        if name == head:
            break
    return f"pr-{head_sha[:12]}"


# ------------------------------------------------------------------ main
def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="merge の前提の検査を、 PR で動く workflow と同じ段で手元で流す")
    ap.add_argument("repo", help="検査する Repository の clone（作業ツリーの未 commit の変更は入らない）")
    ap.add_argument("--head", default="HEAD", help="検査する commit（既定 HEAD。 merge する PR の head）")
    ap.add_argument("--base", default="origin/main", help="比べる基準（paths の判定と BASE_SHA。 既定 origin/main）")
    ap.add_argument("--canonical", default=str(HERE.parents[1]), help="正本（MirumeAI/.github）の clone")
    ap.add_argument("--canonical-ref", default="origin/main", help="正本の道具と再利用 workflow を取る ref")
    ap.add_argument("--python", action="append", default=[], metavar="版=道",
                    help="workflow の Python の版の interpreter（既定は PATH の python<版>）")
    ap.add_argument("--work", help="clone・venv・仮の HOME を置く空の場所（既定は一時の dir）")
    ap.add_argument("--list", action="store_true", help="流す workflow・job・段を出すだけ")
    ap.add_argument("--keep", action="store_true", help="clone・venv・仮の HOME を消さずに残す")
    a = ap.parse_args(argv)
    _bootstrap_yaml()
    try:
        a.target = Path(_git(Path(a.repo), "rev-parse", "--show-toplevel"))
        a.head_sha = _git(a.target, "rev-parse", "--verify", f"{a.head}^{{commit}}")
        base_sha = _git(a.target, "rev-parse", "--verify", f"{a.base}^{{commit}}")
        a.canonical = Path(_git(Path(a.canonical), "rev-parse", "--show-toplevel"))
        a.canonical_sha = _git(a.canonical, "rev-parse", "--verify", f"{a.canonical_ref}^{{commit}}")
    except Unsupported as e:
        print(f"流せません: {e}（基準の ref が無いときは、 先に git fetch してください）", file=sys.stderr)
        return 2
    a.target_slug, a.origin_url = _slug(a.target)
    a.head_ref = _head_ref(a.target, a.head, a.head_sha)
    merge_base = _git(a.target, "merge-base", base_sha, a.head_sha, check=False)
    if not merge_base:
        print(f"流せません: {a.base} と head に共通の祖先がありません", file=sys.stderr)
        return 2
    changed = _git_paths(a.target, "diff", "--no-renames", "--name-only", merge_base, a.head_sha)
    base_branch = re.sub(r"^(?:refs/remotes/)?origin/", "", a.base)
    has_base = merge_base == base_sha
    tool_blob = _blob_sha(Path(__file__).resolve().read_bytes())
    canon_blob = _git(a.canonical, "rev-parse", f"{a.canonical_sha}:{TOOL_REL}", check=False)
    tool_note = f"道具 {tool_blob[:12]}（{'正本と同じ' if tool_blob == canon_blob else '正本と違う'}）"
    base_note = f"基準 {a.base} {base_sha[:12]} を含む: {'はい' if has_base else 'いいえ'}"
    canon_note = f"正本の道具 {CANONICAL_SLUG} {a.canonical_ref} {a.canonical_sha[:12]}"
    # github.head_ref は入れない（手元では分からない値を、 違う値で評価しない）
    probe = {"github": {"workspace": "", "event_name": EVENT, "sha": a.head_sha, "base_ref": base_branch,
                        "repository": a.target_slug, "repository_owner": a.target_slug.split("/")[0],
                        "event": {"pull_request": {"base": {"sha": base_sha, "ref": base_branch},
                                                   "head": {"sha": a.head_sha}}}},
             "runner": {"os": "Linux", "temp": ""}}
    try:
        plan = plan_for(a.target, a.head_sha, a.canonical, a.canonical_sha, base_branch, changed, a.target_slug)
        if not plan.jobs:
            why = "、 ".join(f"{w}（{r}）" for w, r in plan.skipped) or "workflow がありません"
            raise Unsupported(f"pull_request で動く workflow がありません（PASS とは言えません）。 対象外: {why}")
        jobs = _order(plan.jobs)
        _precheck(jobs, probe)
        pythons = {} if a.list else _python_map(plan.pythons, a.python)
    except Unsupported as e:
        print(f"流せません: {e}", file=sys.stderr)
        return 2

    print(f"===== Required Verification: {a.target_slug}")
    print(f"head: {a.head_sha}（{base_note}）")
    print(f"{canon_note}・{tool_note}")
    print(f"変更のファイル: {len(changed)}（merge-base {merge_base[:12]}）")
    if not has_base:
        print(f"注意: head は {a.base} の先を含みません。 GitHub の pull_request は main と合わせた commit で流すので、"
              " merge の前に main を取り込んで流し直してください")
    for wf, why in plan.skipped:
        print(f"対象外: {_show(wf)}（{_show(why)}）")
    if a.list:
        for j in jobs:
            print(f"  {_show(j['workflow'])} の job {_show(j['name'])}: 段 {len(j['spec']['steps'])}"
                  + (f"・needs {[_show(n) for n in j['needs']]}" if j["needs"] else ""))
        return 0

    if a.work and Path(a.work).exists() and any(Path(a.work).iterdir()):
        print(f"流せません: --work の {a.work} が空ではありません（前の結果と混ぜない）", file=sys.stderr)
        return 2
    work = Path(a.work) if a.work else Path(tempfile.mkdtemp(prefix="mirume-rv-"))
    work.mkdir(parents=True, exist_ok=True)
    logs = work / "logs"
    real_cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    results, outcome = {}, {}
    t_all = time.monotonic()
    try:
        for idx, j in enumerate(jobs, 1):
            key = (j["workflow"], j["name"])
            got = [outcome.get((j["workflow"], n)) for n in j["needs"]]
            status = "failure" if "failure" in got else ("skipped" if "skipped" in got else "success")
            ex = Expr({**probe, "inputs": j["inputs"], "env": {}}, status)
            run_it = _cond(j.get("caller_if"), ex) and _cond(j["spec"].get("if"), ex)
            label = f"{_show(j['workflow'])} の job {_show(j['name'])}"
            if not run_it:
                outcome[key] = "skipped"
                why = {"success": "条件で流さない", "skipped": "前の job が流れなかったので流さない",
                       "failure": "前の job が落ちたので流さない"}[status]
                print(f"\n{label}: {why}")
                continue
            t0 = time.monotonic()
            jlog = logs / f"{idx:02d}-{Path(j['workflow']).stem}-{j['name'].replace('/', '-')}"
            jlog.mkdir(parents=True)
            res = _run_job(j, probe, a, work / f"job-{idx:02d}", jlog, pythons, real_cache)
            outcome[key] = res["status"]
            results[label] = res
            print(f"\n{label}: {'PASS' if res['status'] == 'success' else 'FAIL'}（{time.monotonic() - t0:.0f} 秒）")
            for r in res["steps"]:
                tests = (f"・試験 {r['ran']}" + (f"（飛ばした {r['skipped_tests']}）" if r["skipped_tests"] else "")
                         if r["ran"] else "")
                print(f"  {r['n']:02d} {r['result']:<8} {_show(r['name'])}（{r['secs']} 秒{tests}）")
            if not a.keep:
                _rmtree(work / f"job-{idx:02d}")
    except KeyboardInterrupt:
        print("\n止めました", file=sys.stderr)
        return 130
    finally:
        if not a.keep:
            for p in work.glob("job-*"):
                _rmtree(p)
    ran = [s for r in results.values() for s in r["steps"] if s["result"] != "skipped"]
    n_tests = sum(s["ran"] for s in ran)
    n_skip = sum(s["skipped_tests"] for s in ran)
    passed = all(v in ("success", "skipped") for v in outcome.values()) and bool(results)
    summary = {"repository": a.target_slug, "head": a.head_sha, "head_ref": a.head_ref,
               "base": base_sha, "merge_base": merge_base, "head_has_base": has_base,
               "canonical_ref": a.canonical_ref, "canonical": a.canonical_sha,
               "tool_blob": tool_blob, "tool_same_as_canonical": tool_blob == canon_blob,
               "passed": passed, "jobs": results, "skipped_workflows": plan.skipped}
    (logs / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    wfs = len({j["workflow"] for j in jobs})
    detail = (f"{a.target_slug} head {a.head_sha[:12]}・{base_note}・{canon_note}・{tool_note}・"
              f"workflow {wfs}・job {len(results)}・段 {len(ran)}・試験 {n_tests} 件・飛ばした試験 {n_skip} 件・"
              f"{time.monotonic() - t_all:.0f} 秒")
    print()
    if passed:
        print(f"結果: PASS（{detail}）")
        print("記録の行（PR に書く）:")
        print(f"{RECORD_PASS}（{detail}）")
    else:
        failed = [f"{k} の段 {s['n']}" for k, r in results.items() for s in r["steps"]
                  if s["result"] in ("failed", "timeout")]
        print(f"結果: FAIL（{detail}。 落ちた段: {', '.join(failed) or '前の job が落ちた'}）")
    print(f"段ごとの記録: {logs}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())

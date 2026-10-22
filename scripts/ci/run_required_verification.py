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
    workflow を選ぶ（`branches`・`paths`・`paths-ignore` の条件も見る。 変更の
    ファイルは、 `--base` と head の merge-base からの差）
  ・job を `needs` の順に、 段を書いた順に流す。 段が落ちたら、 その job の
    残りは `if: always()` などの段だけを流す（CI と同じ）
  ・再利用 workflow（`MirumeAI/.github/...@main`）と正本の道具は、 この道具を
    置いた clone の `--canonical-ref`（既定 origin/main）から取る。
    `actions/checkout` は、 head か正本の新しい clone に置き換える
  ・**job ごとに、 新しい clone・workflow の Python の版の新しい venv・仮の HOME
    で流す。** 元の clone と HOME は変えない（pip と Playwright の cache だけ共有）。
    **PR のコードに、 手元の認証（token・ssh の agent）を渡さない**
  ・**知らない書き方は、 流す前に止める。** 知らない action・式・matrix などを
    黙って飛ばすと、 段の抜けた「PASS」になる

結果は段ごとの終了コード・試験の件数・飛ばした試験の件数で、 PASS のときは
PR に書く記録の行を出す。 終了コード: 0 PASS ／ 1 FAIL ／ 2 流せない
（書き方・Python の版・引数・対象の workflow が無い）。

**PR のコードを手元で動かす。** 中身を読んでから流す。 仮の HOME と環境変数の
取り除きは、 手元の設定や認証を**うっかり**使わせないためのもので、 同じ OS の
ユーザーで動くコードを閉じ込める箱ではない（本物の HOME にも書けてしまう）。
"""
from __future__ import annotations

import argparse
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
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None          # 無ければ、 道具用の venv を作って流し直す（_bootstrap_yaml）

HERE = Path(__file__).resolve().parent
CANONICAL_SLUG = "MirumeAI/.github"
EVENT = "pull_request"
RECORD_PASS = "GitHub Actions unavailable — canonical verification PASS"

#: 再利用 workflow は正本の main だけ。 ほかの Repository や枝を指すものは、
#: 手元で同じものを取れる保証が無いので止める。
REUSABLE = re.compile(r"^MirumeAI/\.github/(\.github/workflows/[^@/]+\.ya?ml)@main$")

WORKFLOW_KEYS = {"name", "on", "permissions", "jobs", "env", "defaults", "concurrency", "run-name"}
JOB_KEYS = {"name", "runs-on", "timeout-minutes", "steps", "needs", "if", "env",
            "permissions", "defaults", "uses", "with", "concurrency"}
STEP_KEYS = {"name", "id", "uses", "with", "run", "env", "if", "shell",
             "working-directory", "timeout-minutes", "continue-on-error"}
PR_FILTER_KEYS = {"branches", "branches-ignore", "paths", "paths-ignore", "types"}

#: PR のコードへ渡さない環境変数。 CI の job にも無いもの。
_SECRET_NAME = re.compile(r"(?i)(token|secret|passw|api_?key|access_?key|private_?key|credential|auth|session|cookie)")
_DROP_ENV = {"SSH_AUTH_SOCK", "SSH_ASKPASS", "GIT_ASKPASS", "VIRTUAL_ENV", "PYTHONPATH",
             "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "XDG_CONFIG_HOME",
             "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"}


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
    変わったまま流れる。 status の関数は job の今の状態（success / failure）で決まる。
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
        v = self._not()
        while self._peek() == ("op", "&&"):
            self._take()
            rhs = self._not()
            v = rhs if _truthy(v) else v
        return v

    def _not(self):
        if self._peek() == ("op", "!"):
            self._take()
            return not _truthy(self._not())
        return self._cmp()

    def _cmp(self):
        v = self._primary()
        if self._peek() in (("op", "=="), ("op", "!=")):
            op = self._take()
            rhs = self._primary()
            return _equal(v, rhs) if op == "==" else not _equal(v, rhs)
        return v

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
        p = str(p)
        if p.startswith("!"):
            if _glob(p[1:]).match(name):
                hit = False
        elif _glob(p).match(name):
            hit = True
    return hit


def _pr_filter(on) -> dict | None:
    """workflow が pull_request で動くなら、 その条件。 動かないなら None。"""
    if isinstance(on, str):
        return {} if on == EVENT else None
    if isinstance(on, list):
        return {} if EVENT in on else None
    if isinstance(on, dict):
        if EVENT not in on:
            return None
        cfg = on[EVENT] or {}
        if not isinstance(cfg, dict) or set(cfg) - PR_FILTER_KEYS:
            raise Unsupported(f"pull_request の条件を読めません: {cfg!r}")
        return cfg
    raise Unsupported(f"on: を読めません: {on!r}")


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


def _slug(repo: Path) -> tuple:
    url = _git(repo, "remote", "get-url", "origin", check=False)
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    return (f"{m.group(1)}/{m.group(2)}" if m else f"local/{repo.name}"), (url or None)


def _clone(src: Path, sha: str, dest: Path, origin_url: str | None) -> None:
    """head の新しい clone。 元の clone の作業ツリー・ref・設定は変えない。

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


# ------------------------------------------------------------------ 計画
class Plan:
    """流す workflow・job・段を、 流す前に全部読んで確かめたもの。"""

    def __init__(self):
        self.jobs: list = []        # dict（下の _job_entry）
        self.skipped: list = []     # (workflow, 理由)
        self.pythons: set = set()


def _load_yaml(text: str, where: str) -> dict:
    d = yaml.safe_load(text)
    if not isinstance(d, dict):
        raise Unsupported(f"{where}: workflow を読めません")
    if True in d and "on" not in d:          # YAML 1.1 では on: が true になる
        d["on"] = d.pop(True)
    return d


def _check_workflow_keys(d: dict, where: str) -> None:
    unknown = set(d) - WORKFLOW_KEYS
    if unknown:
        raise Unsupported(f"{where}: 知らない鍵 {sorted(unknown)}")


def _check_steps(steps, where: str, target_slug: str) -> None:
    if not isinstance(steps, list) or not steps:
        raise Unsupported(f"{where}: steps がありません")
    for i, s in enumerate(steps):
        w = f"{where} の段 {i + 1}"
        if not isinstance(s, dict) or set(s) - STEP_KEYS:
            raise Unsupported(f"{w}: 知らない鍵 {sorted(set(s) - STEP_KEYS) if isinstance(s, dict) else s!r}")
        if ("run" in s) == ("uses" in s):
            raise Unsupported(f"{w}: run と uses のどちらか 1 つにしてください")
        if s.get("shell") not in (None, "bash", "sh"):
            raise Unsupported(f"{w}: shell {s.get('shell')!r} は流せません")
        if not isinstance(s.get("timeout-minutes", 1), (int, float)) or isinstance(s.get("timeout-minutes"), bool):
            raise Unsupported(f"{w}: timeout-minutes は数にしてください")
        if not isinstance(s.get("continue-on-error", False), bool):
            raise Unsupported(f"{w}: continue-on-error は true / false にしてください")
        uses = s.get("uses")
        if uses is None:
            continue
        action = str(uses).split("@")[0]
        with_ = s.get("with") or {}
        if action == "actions/checkout":
            extra = set(with_) - {"path", "repository", "fetch-depth"}
            repo = with_.get("repository")
            if extra or repo not in (None, target_slug, CANONICAL_SLUG):
                raise Unsupported(f"{w}: この checkout は流せません（{dict(with_)}）")
        elif action == "actions/setup-python":
            if set(with_) - {"python-version", "cache"} or not with_.get("python-version"):
                raise Unsupported(f"{w}: この setup-python は流せません（{dict(with_)}）")
        else:
            raise Unsupported(f"{w}: action {uses} は流せません")


def _job_entry(wf: str, name: str, spec: dict, inputs: dict, needs: list, where: str,
               plan: Plan, target_slug: str) -> dict:
    unknown = set(spec) - JOB_KEYS
    if unknown:
        raise Unsupported(f"{where}: 知らない鍵 {sorted(unknown)}")
    runs_on = spec.get("runs-on")
    if not (isinstance(runs_on, str) and runs_on.startswith("ubuntu-")):
        raise Unsupported(f"{where}: runs-on {runs_on!r} は流せません（ubuntu だけ）")
    t = spec.get("timeout-minutes", 1)
    if not isinstance(t, (int, float)) or isinstance(t, bool):
        raise Unsupported(f"{where}: timeout-minutes は数にしてください")
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
    declared = call_spec.get("inputs") or {}
    unknown = set(with_) - set(declared)
    if unknown:
        raise Unsupported(f"再利用 workflow に無い入力: {sorted(unknown)}")
    out = {}
    for k, d in declared.items():
        if (d or {}).get("required") and k not in with_ and "default" not in (d or {}):
            raise Unsupported(f"再利用 workflow の入力 {k} が渡されていません")
        v = with_.get(k, (d or {}).get("default"))
        if (d or {}).get("type") == "boolean" and isinstance(v, str):
            v = v.strip().lower() == "true"
        out[k] = v
    return out


def plan_for(target: Path, head: str, canonical: Path, canonical_sha: str, base_branch: str,
             changed: list, target_slug: str) -> Plan:
    plan = Plan()
    listing = _git(target, "ls-tree", "--name-only", head, ".github/workflows/", check=False)
    files = sorted(f for f in listing.splitlines() if f.endswith((".yml", ".yaml")))
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
        _check_workflow_keys(d, wf)
        jobs = d.get("jobs") or {}
        for jn, spec in jobs.items():
            if not isinstance(spec, dict):
                raise Unsupported(f"{wf} の job {jn} を読めません")
            needs = spec.get("needs") or []
            needs = [needs] if isinstance(needs, str) else list(needs)
            if "uses" in spec:
                m = REUSABLE.match(str(spec["uses"]))
                if not m or set(spec) - {"uses", "with", "needs", "if", "permissions", "name"}:
                    raise Unsupported(f"{wf} の job {jn}: 再利用 workflow {spec.get('uses')} は流せません")
                rw = _load_yaml(_git(canonical, "show", f"{canonical_sha}:{m.group(1)}"), m.group(1))
                _check_workflow_keys(rw, m.group(1))
                on = rw.get("on")
                if not (isinstance(on, dict) and "workflow_call" in on):
                    raise Unsupported(f"{m.group(1)} は workflow_call で動きません")
                inputs = _inputs(on["workflow_call"] or {}, spec.get("with") or {})
                inner = rw.get("jobs") or {}
                for ij, ispec in inner.items():
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


# ------------------------------------------------------------------ 流す
_RAN = re.compile(r"^Ran (\d+) tests? in ", re.M)
_SKIP = re.compile(r"^(?:OK|FAILED) \(.*?skipped=(\d+)", re.M)
#: guard の回帰テスト（scripts/ci/test_guard_main.sh）の数え方
_GUARD = re.compile(r"^合格 (\d+) / 不合格 (\d+)$", re.M)


def _counts(log: str) -> tuple:
    ran = sum(map(int, _RAN.findall(log))) + sum(int(a) + int(b) for a, b in _GUARD.findall(log))
    return ran, sum(map(int, _SKIP.findall(log)))


def _base_env(real_cache: Path) -> dict:
    env = {}
    for k, v in os.environ.items():
        if k in _DROP_ENV or _SECRET_NAME.search(k) or k.startswith(("GITHUB_", "RUNNER_", "CLAUDE")):
            continue
        env[k] = v
    env.setdefault("PIP_CACHE_DIR", str(real_cache / "pip"))
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(real_cache / "ms-playwright"))
    if not env.get("LANG", "").lower().endswith(("utf-8", "utf8")):
        env["LANG"] = "C.UTF-8"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["CI"] = "true"
    env["GITHUB_ACTIONS"] = "true"
    env["RUNNER_OS"] = "Linux"
    return env


def _apply_github_files(env: dict, files: dict) -> None:
    """段が GITHUB_ENV・GITHUB_PATH に書いたものを、 次の段へ渡す。"""
    text = files["GITHUB_ENV"].read_text(encoding="utf-8")
    lines, i = text.splitlines(), 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)<<(.+)$", ln)
        if m:
            end = lines.index(m.group(2), i + 1) if m.group(2) in lines[i + 1:] else None
            if end is None:
                raise RuntimeError("GITHUB_ENV の区切りが閉じていません")
            env[m.group(1)] = "\n".join(lines[i + 1:end])
            i = end + 1
            continue
        if "=" in ln:
            k, v = ln.split("=", 1)
            env[k] = v
        i += 1
    for p in reversed(files["GITHUB_PATH"].read_text(encoding="utf-8").splitlines()):
        if p.strip():
            env["PATH"] = p.strip() + os.pathsep + env["PATH"]
    files["GITHUB_ENV"].write_text("")
    files["GITHUB_PATH"].write_text("")


def _kill(proc: subprocess.Popen) -> None:
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=wait)
            return
        except subprocess.TimeoutExpired:
            continue


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
                "GITHUB_HEAD_REF": "", **{k: str(v) for k, v in files.items()}})
    ctx = json.loads(json.dumps(ctx_base))
    ctx["github"]["workspace"] = str(ws)
    ctx["runner"]["temp"] = str(temp)
    ctx["inputs"] = job["inputs"]
    wenv = {k: _subst(str(v), Expr({**ctx, "env": env})) for k, v in job["workflow_env"].items()}
    env.update(wenv)
    jenv = {k: _subst(str(v), Expr({**ctx, "env": env})) for k, v in (spec.get("env") or {}).items()}
    env.update(jenv)
    timeout = float(spec.get("timeout-minutes") or 360) * 60
    deadline = time.monotonic() + timeout
    status, results = "success", []
    for i, step in enumerate(spec["steps"], 1):
        name = step.get("name") or step.get("uses") or str(step.get("run", "")).strip().splitlines()[0][:60]
        ex = Expr({**ctx, "env": env}, status)
        r = {"n": i, "name": name, "result": "skipped", "secs": 0.0, "ran": 0, "skipped_tests": 0}
        results.append(r)
        if not _cond(step.get("if"), ex):
            continue
        t0 = time.monotonic()
        senv = dict(env)
        senv.update({k: _subst(str(v), ex) for k, v in (step.get("env") or {}).items()})
        log = logdir / f"{i:02d}.log"
        try:
            ok = _run_step(step, ex, senv, ws, temp, log, a, pythons, jobdir, env, deadline,
                           float(step["timeout-minutes"]) * 60 if step.get("timeout-minutes") else None)
        except RuntimeError as e:
            log.write_text(f"{e}\n", encoding="utf-8")
            ok = False
        r["secs"] = round(time.monotonic() - t0, 1)
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        r["ran"], r["skipped_tests"] = _counts(text)
        if ok:
            r["result"] = "ok"
            _apply_github_files(env, files)
        elif step.get("continue-on-error") is True:
            r["result"] = "failed（続けた）"
        else:
            r["result"] = "timeout" if time.monotonic() >= deadline else "failed"
            status = "failure"
    return {"status": status, "steps": results}


def _run_step(step, ex, senv, ws, temp, log, a, pythons, jobdir, env, deadline, step_timeout) -> bool:
    uses = step.get("uses")
    with_ = {k: _subst(str(v), ex) for k, v in (step.get("with") or {}).items()}
    if uses:
        action = uses.split("@")[0]
        if action == "actions/checkout":
            dest = ws / with_["path"] if with_.get("path") else ws
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
    cmd = (["sh", "-e", str(script)] if step.get("shell") == "sh"
           else ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)])
    cwd = ws / _subst(step["working-directory"], ex) if step.get("working-directory") else ws
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
            _kill(proc)
            fh.write(f"\n時間の上限（{limit:.0f} 秒）を過ぎたので止めました\n".encode())
            return False
        except KeyboardInterrupt:
            _kill(proc)
            raise
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
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD | stat.S_IEXEC)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=force)


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
    ap.add_argument("--work", help="clone・venv・仮の HOME を置く場所（既定は一時の dir）")
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
    merge_base = _git(a.target, "merge-base", base_sha, a.head_sha, check=False)
    if not merge_base:
        print(f"流せません: {a.base} と head に共通の祖先がありません", file=sys.stderr)
        return 2
    changed = [f for f in _git(a.target, "diff", "--name-only", merge_base, a.head_sha).splitlines() if f]
    base_branch = re.sub(r"^(?:refs/remotes/)?origin/", "", a.base)
    up_to_date = subprocess.run(["git", "-C", str(a.target), "merge-base", "--is-ancestor", base_sha,
                                 a.head_sha]).returncode == 0
    try:
        plan = plan_for(a.target, a.head_sha, a.canonical, a.canonical_sha, base_branch, changed, a.target_slug)
        if not plan.jobs:
            why = "、 ".join(f"{w}（{r}）" for w, r in plan.skipped) or "workflow がありません"
            raise Unsupported(f"pull_request で動く workflow がありません（PASS とは言えません）。 対象外: {why}")
        jobs = _order(plan.jobs)
        pythons = {} if a.list else _python_map(plan.pythons, a.python)
        # 式を流す前に読めるか確かめる（知らない値で止まるなら、 何も流さないうちに止める）
        probe = {"github": {"workspace": "", "event_name": EVENT, "sha": a.head_sha, "base_ref": base_branch,
                            "head_ref": "", "repository": a.target_slug, "repository_owner": a.target_slug.split("/")[0],
                            "event": {"pull_request": {"base": {"sha": base_sha, "ref": base_branch},
                                                       "head": {"sha": a.head_sha}}}},
                 "runner": {"os": "Linux", "temp": ""}}
        for j in jobs:
            ex = Expr({**probe, "inputs": j["inputs"], "env": {}})
            for raw in [j["spec"].get("if"), j.get("caller_if")]:
                if raw is not None and not isinstance(raw, bool):
                    _cond(raw, ex)
            for s in j["spec"]["steps"]:
                for v in [s.get("run"), s.get("working-directory"), *((s.get("env") or {}).values()),
                          *((s.get("with") or {}).values())]:
                    if isinstance(v, str):
                        _subst(v, ex)
                if s.get("if") is not None and not isinstance(s.get("if"), bool):
                    _cond(s["if"], ex)
    except Unsupported as e:
        print(f"流せません: {e}", file=sys.stderr)
        return 2

    print(f"===== Required Verification: {a.target_slug}")
    print(f"head: {a.head_sha}（{a.base} は head の祖先: {'はい' if up_to_date else 'いいえ'}）")
    print(f"正本の道具: {CANONICAL_SLUG} {a.canonical_sha[:12]}（{a.canonical_ref}）")
    print(f"変更のファイル: {len(changed)}（merge-base {merge_base[:12]}）")
    for wf, why in plan.skipped:
        print(f"対象外: {wf}（{why}）")
    if a.list:
        for j in jobs:
            print(f"  {j['workflow']} の job {j['name']}: 段 {len(j['spec']['steps'])}"
                  + (f"・needs {j['needs']}" if j["needs"] else ""))
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
            label = f"{j['workflow']} の job {j['name']}"
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
            secs = time.monotonic() - t0
            print(f"\n{label}: {'PASS' if res['status'] == 'success' else 'FAIL'}（{secs:.0f} 秒）")
            for r in res["steps"]:
                tests = f"・試験 {r['ran']}" + (f"（飛ばした {r['skipped_tests']}）" if r["skipped_tests"] else "") if r["ran"] else ""
                print(f"  {r['n']:02d} {r['result']:<8} {r['name']}（{r['secs']} 秒{tests}）")
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
    summary = {"repository": a.target_slug, "head": a.head_sha, "canonical": a.canonical_sha,
               "base": base_sha, "merge_base": merge_base, "up_to_date": up_to_date,
               "passed": passed, "jobs": results, "skipped_workflows": plan.skipped}
    (logs / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    wfs = len({j["workflow"] for j in jobs})
    detail = (f"{a.target_slug} head {a.head_sha[:12]}・正本の道具 {CANONICAL_SLUG} {a.canonical_sha[:12]}・"
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

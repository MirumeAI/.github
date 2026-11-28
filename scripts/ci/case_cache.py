#!/usr/bin/env python3
"""案件のフォルダの `.cache/` と、 共通の道具の cache（利用者の cache の下の `mirumeai/`）の、 使い終えた物を片付ける。

既定は**見るだけ**で、 何も消さない。 消せる物・残す物（理由つき）・知らせる物を出す。
`--apply` を付けたときだけ、 消せる物を消す。

    python3 scripts/ci/case_cache.py <案件のフォルダ>                 # 見るだけ
    python3 scripts/ci/case_cache.py <案件のフォルダ> --apply         # 消せる物だけを消す
    python3 scripts/ci/case_cache.py <案件のフォルダ> --keep <名前>   # `.cache/` に置いてよい物を、 この案件で足す（何度でも）

なぜ要るのか。 検査の記録（1 回で数ファイル）・検査の作業の場所・Pull Request や Issue の下書きが、
案件のフォルダの `.cache/` と利用者の cache にたまり続けた（ある案件で、 下書き約 860・検査の記録約 470・
作業の場所 92。 #247）。 片付けは Agent の心がけと案件ごとの手作りの包みに頼っていて、 ほかの案件の
セッションでは同じことが起きる。 決まり（development-docs の目標構造の `.cache/` の行）は、 Required
Verification の出力は Pull Request の merge の後に消してよい、 git の clone・作業ツリー・仮想環境は置かない、 と書く。

見るもの:

  1. **検査の記録**（`.cache/rv-logs/` の直下の `<名前>.log` と、 それに付く `<名前>.log.jobs`・`<名前>.log.outer`）。
     記録は `run_required_verification.py` の出力で、 頭の `===== Required Verification: <owner/repo>` と次の行の
     `head: <40 桁>` から、 その commit に結び付いた Pull Request と state・今の head を GitHub に問う
     （`gh api --method GET repos/<owner>/<repo>/commits/<head>/pulls`。 読むだけ）。
     消せるのは、 結び付いた Pull Request がどれも merge 済みか、 開いていても今の head が記録の head でない
     記録だけ（**merge された Pull Request の記録と、 開いた Pull Request の古い head の記録**）。 merge せずに
     閉じた Pull Request の head には、 GitHub がこの問いで Pull Request を返さない（実物で確かめた。 答えは空）
     ので、 閉じただけの記録は「Pull Request が見つからない」として残る（消すとは約束しない。 返ってきても消さない）。
     Pull Request が見つからない（push していない head・閉じただけの Pull Request など）・gh が使えない・gh の答えの
     形が違う（Repository・state・番号・開いた Pull Request の head）・頭が読めない（頭が 1 つでない・GitHub の Repository でない）
     記録は、 理由を出して残す（安全な側）。
     この利用者の Required Verification の道具が流れている間（流れているか分からないときも）は、 終わりの行
     （`段ごとの記録: …` か、 包みが足す `rc=<数>`）の無い記録を残す（書き途中の記録を消さない）
  2. **作業の場所**。 消す記録が名指しする作業の場所（`段ごとの記録: <道>/logs` の `<道>`）は、 利用者の cache の
     下の `mirumeai/w/<数>`（案件の包みが作る）か `mirumeai/sw/<名前>`（写しの同期の道具が作る）で、 そこまでに
     symlink が無く、 この利用者の dir で、 中の `logs/summary.json` の Repository と head が記録と同じときだけ、
     記録と一緒に消す（作業の場所の名前は使い回されるので、 別の検査の物を消さない。 流れている検査の場所には
     まだ `summary.json` が無い）。 残す記録も名指しする作業の場所は消さない。 ほかの道なら、 消さずに知らせる
  3. **置かない物**（知らせるだけ。 消さない）。 `.cache/` の直下の物のうち、 決まった物（`KNOWN`。 案件ごとに
     `--keep` で足せる）でない物を「決まりの一覧に無い」として、 大きさとファイルの数を出す。 中に git の
     clone・作業ツリー（`.git`）や仮想環境（`pyvenv.cfg`）があれば、 決まった物の中でも「`.cache/` に置かない物」と出す。
     中を数えるときは symlink を辿らず、 別の file system に入らない
  4. **共通の cache**（知らせるだけ。 消さない）。 利用者の cache の下の `mirumeai/` の直下の物が `SHARED_LARGE`
     より大きければ、 大きさと片付け方（写しの同期の道具の `cleanup` など。 `SHARED_HINT`）を出す

**消すのは、 `.cache/rv-logs/` の直下の、 この利用者の普通のファイル（symlink でない）と、 上の 2 の条件の作業の
場所だけ。** 作業ルート（`*_data`）・`.venvs`・`config`・`weights`・`wheelhouse`・clone など、 `.cache/` の外の物には
触れない（作業の場所のほか）。 `.cache/` か `rv-logs/` が symlink なら、 その中を見ない。 消す直前に、 見たときと
同じ物か（symlink でない・持ち主・inode。 作業の場所は上の 2 の条件も）を確かめ直し、 違えばその物を消さずに
誤りにする（終了コード 1）。 作業の場所の中に、 本人が入れない dir（持ち主の読みか入りの権限が無い。 mode 000・300
など）・ほかの利用者の dir・別の file system があれば、 中を何も消さずに、 作業の場所と記録を残して誤りにする
（権限を直してから流し直す）。 作業の場所が消せなければ、 その記録も残す（次に流し直せる）。 誤りがあっても、
ほかの記録は続けて扱う。 消し終えたことを確かめた物だけを、 消した数に数える。

利用者の cache は `XDG_CACHE_HOME`（無ければ `~/.cache`）。 `run_required_verification.py` のロックの置き場と同じ。

出力に出す物と出さない物。 **出力には記録のファイルの名前と `.cache/` の下の名前が出るので、 GitHub（Issue・
Pull Request・コメント・commit の文）にそのまま写さない**（写すのは数だけにする）。

  ・出す: 記録のファイルの名前（案件の包みは Repository の短い名前から付けるので、 顧客 Repository の短い名前が
    入りうる）・head の 12 桁・Pull Request の番号・`.cache/` の下の名前と道（案件のフォルダからの相対。 中の
    clone・仮想環境の道も）・作業の場所と共通の cache の置き場（利用者の cache からの相対）・大きさとファイルの数
  ・出さない: 案件のフォルダの道（「案件のフォルダ」とだけ書く）・利用者の cache の道（`XDG_CACHE_HOME` か
    `~/.cache` とだけ書く）・記録の頭の Repository の名前（`owner/repo`）・gh の誤りの文（道に Repository の名前が
    入る）・決まった置き場の外の作業の場所の道（記録の終わりの行を見る）・OS の誤りに付く道

終了コード: 0 見るだけ・消し終えた（消せる物が無いときも） ／ 1 消す途中の誤り ／ 2 案件のフォルダが無い・引数の誤り。

このリポジトリは **Public**。 顧客名・社内の道をここに書かない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

#: 案件のフォルダの `.cache/` の直下に置いてよい物。 案件ごとに足すときは `--keep`
KNOWN = ("rv-logs", "rv.lock", "nas", "screen-walk", "make_venvs")
CACHE = ".cache"
RV_LOGS = "rv-logs"
#: 検査の記録に付くファイル（案件の包みが書く `<名前>.log<これ>`）
COMPANIONS = (".jobs", ".outer")
#: 読む記録の大きさの上限（これより大きい記録は読まずに残す）
MAX_LOG = 16 * 1024 * 1024
#: 読む `logs/summary.json` の大きさの上限
MAX_SUMMARY = 4 * 1024 * 1024
#: 共通の cache で、 これより大きい物を知らせる
SHARED_LARGE = 1024 ** 3
#: 共通の cache の直下の物の片付け方
SHARED_HINT = {
    "sync-copies": "写しの同期の道具の状態の置き場。 merge された同期は、 その道具の cleanup で片付ける"
                   "（python3 scripts/ci/sync_copies.py cleanup --source <正本の Pull Request の番号>）",
    "sw": "写しの同期の道具の検査の作業の置き場。 その道具の cleanup で片付ける"
          "（python3 scripts/ci/sync_copies.py cleanup --source <正本の Pull Request の番号>）",
    "w": "検査の作業の場所。 記録が名指しする物は、 その記録を持つ案件のフォルダで、 この道具が片付ける",
}
#: 作業の場所の置き場（利用者の cache の下の `mirumeai/<これ>/<名前>`）と、 名前の形
WORK_PARENTS = {"w": re.compile(r"[0-9]{1,9}"), "sw": re.compile(r"[a-z0-9]{1,32}")}
#: Required Verification の道具の名前（流れているかを見る）
RV_TOOL = "run_required_verification.py"
#: gh を待つ秒
GH_TIMEOUT = 60

HEADER = re.compile(r"===== Required Verification: (?P<slug>.*)")
HEAD = re.compile(r"head: (?P<sha>[0-9a-f]{40})(?:（.*)?")
STEP_LOGS = re.compile(r"段ごとの記録: (?P<dir>.*)")
RC_LINE = re.compile(r"rc=[0-9]+")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SHA = re.compile(r"[0-9a-f]{40}")
#: 出力の行を崩しうる字の種類（`run_required_verification.py` と同じ）
BREAKING = ("Cc", "Cf", "Zl", "Zp", "Cs")


def _uid() -> int:
    """この利用者（試験で差し替える）。"""
    return os.getuid()


def _show(name) -> str:
    """出力に出す名前。 行を崩しうる字は \\uXXXX にし、 `\\` そのものは `\\\\` にする。"""
    return "".join("\\\\" if c == "\\" else
                   (f"\\u{ord(c):04x}" if ord(c) <= 0xFFFF else f"\\U{ord(c):08x}")
                   if unicodedata.category(c) in BREAKING else c for c in str(name))


def _size(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        n /= 1024
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} TiB"


def _lstat(p) -> "os.stat_result | None":
    try:
        return os.lstat(p)
    except OSError:
        return None


def _du(st: os.stat_result) -> int:
    blocks = getattr(st, "st_blocks", None)
    return blocks * 512 if blocks is not None else st.st_size


def _same(a: "os.stat_result | None", b: "os.stat_result | None") -> bool:
    return a is not None and b is not None and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _user_cache() -> Path:
    return Path(os.path.abspath(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"))


def _own_dir(p: Path) -> str:
    """この利用者の dir（symlink でない）なら ""、 そうでなければ理由。"""
    st = _lstat(p)
    if st is None:
        return "無い"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not stat.S_ISDIR(st.st_mode):
        return "dir でない"
    if st.st_uid != _uid():
        return "この利用者の物でない"
    return ""


def _own_file(st: "os.stat_result | None") -> str:
    """この利用者の普通のファイル（symlink でない）なら ""、 そうでなければ理由。"""
    if st is None:
        return "無い"
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    if not stat.S_ISREG(st.st_mode):
        return "普通のファイルでない"
    if st.st_uid != _uid():
        return "この利用者の物でない"
    return ""


def _read_nofollow(p: Path, limit: int) -> "bytes | None":
    """symlink を辿らずに読む。 上限より大きい・読めないときは None。"""
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            data = f.read(limit + 1)
    except OSError:
        return None
    return None if len(data) > limit else data


def checks_running() -> "bool | None":
    """この利用者の Required Verification の道具が流れているか。 分からないときは None。"""
    me, uid = os.getpid(), _uid()
    try:
        pids = [n for n in os.listdir("/proc") if n.isdigit()]
    except OSError:
        return None
    for pid in pids:
        if int(pid) == me:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            continue                    # 終わった process・読めない process
        if any(os.path.basename(x.decode("utf-8", "replace")) == RV_TOOL for x in argv):
            return True
    return False


# ------------------------------------------------------------------ GitHub
class GitHub:
    """commit に結び付いた Pull Request を gh で読む（読むだけ。 同じ問いは 1 回）。"""

    def __init__(self) -> None:
        self.answers: dict = {}
        self.broken = ""                # gh が使えない理由（その後は問わない）

    def pulls(self, slug: str, sha: str) -> "tuple[list | None, str]":
        if self.broken:
            return None, self.broken
        key = (slug.lower(), sha)
        if key not in self.answers:
            self.answers[key] = self._ask(slug, sha)
        return self.answers[key]

    def _ask(self, slug: str, sha: str) -> "tuple[list | None, str]":
        try:
            p = subprocess.run(["gh", "api", "--method", "GET", f"repos/{slug}/commits/{sha}/pulls"],
                               capture_output=True, text=True, timeout=GH_TIMEOUT, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            self.broken = "gh が使えない（gh が無い）"
            return None, self.broken
        except subprocess.TimeoutExpired:
            return None, f"gh が {GH_TIMEOUT} 秒で答えない"
        except OSError as e:
            self.broken = f"gh が使えない（{_why(e)}）"
            return None, self.broken
        if p.returncode:
            # 誤りの文は出さない（道に Repository の名前が入る）。 種類だけにする
            err = p.stderr or ""
            if "HTTP 422" in err or "No commit found" in err:
                return None, "GitHub にこの head が無い（push していない・消えた head）"
            if "HTTP 404" in err:
                return None, "Repository かこの head を読めない（HTTP 404）"
            if "HTTP 401" in err or "auth login" in err or "authentication" in err.lower():
                self.broken = "gh が使えない（認証）"
                return None, self.broken
            return None, f"gh が誤りを返した（終了コード {p.returncode}）"
        try:
            data = json.loads(p.stdout)
        except ValueError:
            data = None
        if not isinstance(data, list):
            return None, "gh の答えが読めない"
        return data, ""


def _dig(d, *keys):
    """入れ子の dict から値を取る（途中が dict でなければ None）。"""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def decide(slug: str, sha: str, pulls: list) -> "tuple[bool, str]":
    """記録を消してよいか（と理由）。 結び付いた Pull Request がどれも merge 済みか、 開いていても今の head が
    記録の head でないときだけ消してよい。 答えの形が違えば（Repository が読めない・state が open・closed の
    ほか・番号が数でない・開いた Pull Request の head が 40 桁の小文字の 16 進でない）消さない。 merge せずに
    閉じた Pull Request は、 GitHub はふつう返さないが、 返っても消さない。"""
    prs = []
    for p in pulls:
        if not isinstance(p, dict):
            return False, "gh の答えが読めない"
        base = _dig(p, "base", "repo", "full_name")
        if not isinstance(base, str):
            return False, "gh の答えが読めない（Pull Request の Repository）"
        if base.lower() != slug.lower():
            continue                    # 別の Repository の Pull Request
        num, state, head = p.get("number"), p.get("state"), _dig(p, "head", "sha")
        if not isinstance(num, int) or isinstance(num, bool) or state not in ("open", "closed"):
            return False, "gh の答えが読めない（Pull Request の番号か state）"
        if state == "open" and not (isinstance(head, str) and SHA.fullmatch(head)):
            return False, "gh の答えが読めない（開いた Pull Request の head）"
        prs.append((num, state, bool(p.get("merged_at")), head))
    if not prs:
        return False, "Pull Request が見つからない"
    for num, state, merged, head in prs:
        if state == "open" and head == sha:
            return False, f"Pull Request #{num} の今の head（開いている）"
        if state == "closed" and not merged:
            return False, f"Pull Request #{num} は merge せずに閉じた（閉じただけの記録は消さない）"
    why = [f"#{num} は merge 済み" if state == "closed" else f"#{num} の今の head は別（{head[:12]}）"
           for num, state, merged, head in prs]
    return True, "Pull Request " + "・".join(why)


# ------------------------------------------------------------------ 検査の記録
@dataclass
class Record:
    name: str
    path: Path
    st: os.stat_result
    companions: list = field(default_factory=list)  # [(道, stat)]
    head: str = ""
    slug: str = ""
    work_text: "str | None" = None   # `段ごとの記録:` の道（`/logs` の前）
    named: list = field(default_factory=list)       # 記録が名指しする作業の場所のすべて（残す記録の守りに使う）
    finished: bool = False
    delete: bool = False
    reason: str = ""
    work: "Path | None" = None       # 一緒に消す作業の場所
    work_st: "os.stat_result | None" = None


def read_log(r: Record) -> str:
    """記録の頭と終わりを読む。 消せない形なら理由を返す（"" なら読めた）。"""
    data = _read_nofollow(r.path, MAX_LOG)
    if data is None:
        return f"読めない（{_size(MAX_LOG)} より大きいか、 開けない）"
    lines = [ln.rstrip("\r") for ln in data.decode("utf-8", "replace").split("\n")]
    steps = [m for m in (STEP_LOGS.fullmatch(ln) for ln in lines) if m]
    r.named = [m["dir"][:-len("/logs")] for m in steps if m["dir"].endswith("/logs")]
    if len(steps) == 1 and r.named:
        r.work_text = r.named[0]
    tail = [ln for ln in lines if ln.strip()][-1:]
    r.finished = bool(steps) or bool(tail and RC_LINE.fullmatch(tail[0]))
    heads = [i for i, ln in enumerate(lines) if HEADER.fullmatch(ln)]
    if not heads:
        return "頭が読めない（Required Verification の記録の形でない）"
    if len(heads) > 1:
        return "頭が 2 つ以上ある（検査の記録が 1 つでない）"
    if len(steps) > 1:
        return "段ごとの記録の行が 2 つ以上ある"
    i = heads[0]
    m = HEAD.fullmatch(lines[i + 1]) if i + 1 < len(lines) else None
    if not m:
        return "head の行が読めない"
    r.head = m["sha"]
    slug = HEADER.fullmatch(lines[i])["slug"]
    if (not SLUG.fullmatch(slug) or slug.lower().startswith("local/")
            or any(not part.strip(".") for part in slug.split("/"))):
        return "GitHub の Repository の記録でない"
    r.slug = slug
    return ""


def work_target(r: Record, roots: list) -> "tuple[Path | None, os.stat_result | None, str]":
    """記録が名指しする作業の場所を、 消してよければ (道, stat, "")、 消さないなら (None, None, 理由)、
    もう無ければ (None, None, "") で返す。"""
    raw = r.work_text or ""
    if not raw.startswith("/") or os.path.normpath(raw) != raw:
        return None, None, "決まった置き場の外"
    p = Path(raw)
    hit = None
    for root in roots:
        for kind, pattern in WORK_PARENTS.items():
            if p.parent == root / "mirumeai" / kind and pattern.fullmatch(p.name):
                hit = (root, kind)
    if hit is None:
        return None, None, "決まった置き場（利用者の cache の下の mirumeai/w/<数>・mirumeai/sw/<名前>）の外"
    root, kind = hit
    for q in (root / "mirumeai", root / "mirumeai" / kind):
        why = _own_dir(q)
        if why:
            return None, None, f"置き場の {q.name}/: {why}"
    st = _lstat(p)
    if st is None:
        return None, None, ""
    why = _own_dir(p)
    if why:
        return None, None, why
    if _own_dir(p / "logs"):
        return None, None, "中の記録（logs/summary.json）が無い（流れている検査が使っている見込み）"
    data = _read_nofollow(p / "logs" / "summary.json", MAX_SUMMARY)
    if data is None:
        return None, None, "中の記録（logs/summary.json）が無い（流れている検査が使っている見込み）"
    try:
        summary = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        summary = None
    if (not isinstance(summary, dict) or summary.get("head") != r.head
            or str(summary.get("repository", "")).lower() != r.slug.lower()):
        return None, None, "中の記録（logs/summary.json）の Repository か head がこの記録と違う（別の検査の場所）"
    return p, st, ""


def scan_logs(rv: Path, gh: GitHub, running: "bool | None", roots: list, out: "Out") -> list:
    """`.cache/rv-logs/` の直下を見て、 記録ごとに消すか残すかを決める。"""
    try:
        with os.scandir(rv) as it:
            entries = {e.name: e for e in it}
    except OSError as e:
        out.notice.append(f".cache/{RV_LOGS}/ を読めない（{_why(e)}）")
        return []
    records, used = [], set()
    for name in sorted(entries):
        if not name.endswith(".log"):
            continue
        used.add(name)
        st = _lstat(rv / name)
        r = Record(name=name, path=rv / name, st=st)
        records.append(r)
        for suffix in COMPANIONS:
            if name + suffix in entries:
                used.add(name + suffix)
                r.companions.append((rv / (name + suffix), _lstat(rv / (name + suffix))))
        why = _own_file(st)
        if why:
            r.reason = why
            continue
        why = read_log(r)
        if why:
            r.reason = why
            continue
        if not r.finished and running is not False:
            r.reason = ("終わりの行が無い（Required Verification が流れているので、 書き途中かもしれない）"
                        if running else "終わりの行が無い（Required Verification が流れているか分からない）")
            continue
        pulls, why = gh.pulls(r.slug, r.head)
        if pulls is None:
            r.reason = why
            continue
        r.delete, r.reason = decide(r.slug, r.head, pulls)
    for name in sorted(set(entries) - used):
        e = entries[name]
        what = ("付属のファイルに対の記録が無い" if any(name.endswith(".log" + s) for s in COMPANIONS)
                else "symlink" if e.is_symlink() else "dir" if e.is_dir(follow_symlinks=False) else "記録の形でない")
        out.other.append(f"{_show(name)}: {what}（消さない）")

    # 作業の場所。 残す記録も名指しする物と、 二度目に名指しされた物は消さない
    def key(text: str) -> str:
        return os.path.realpath(text)

    kept = {key(t) for r in records if not r.delete for t in r.named}
    taken = set()
    for r in records:
        if not (r.delete and r.work_text):
            continue
        p, st, why = work_target(r, roots)
        if p is not None and key(str(p)) in kept:
            p, why = None, "残す記録も名指ししている"
        if p is not None and key(str(p)) in taken:
            continue                    # 上の記録と一緒に消す
        if p is None:
            if why:
                out.notice.append(f"{_show(r.name)} の作業の場所（{out.where(r.work_text)}）は消さない（{why}）")
            continue
        taken.add(key(str(p)))
        r.work, r.work_st = p, st
    return records


# ------------------------------------------------------------------ 置かない物・共通の cache
@dataclass
class Measure:
    size: int = 0
    files: int = 0
    git: list = field(default_factory=list)
    venv: list = field(default_factory=list)
    unread: int = 0
    other_fs: int = 0


def measure(top: Path) -> Measure:
    """大きさ・ファイルの数と、 中の git の clone・作業ツリーと仮想環境。 symlink を辿らず、 別の file system に入らない。"""
    m = Measure()
    st = _lstat(top)
    if st is None:
        return m
    m.size += _du(st)
    if not stat.S_ISDIR(st.st_mode):
        m.files = 1
        return m
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            m.unread += 1
            continue
        with it:
            for e in it:
                try:
                    est = e.stat(follow_symlinks=False)
                except OSError:
                    m.unread += 1
                    continue
                where = Path(e.path).parent.relative_to(top)
                if e.name == ".git" and not stat.S_ISLNK(est.st_mode):
                    m.git.append(where)
                if stat.S_ISDIR(est.st_mode):
                    if est.st_dev != st.st_dev:
                        m.other_fs += 1
                        continue
                    m.size += _du(est)
                    stack.append(Path(e.path))
                    continue
                m.files += 1
                m.size += _du(est)
                if e.name == "pyvenv.cfg" and stat.S_ISREG(est.st_mode):
                    m.venv.append(where)
    return m


def _measured(m: Measure) -> str:
    extra = (f"・読めない所 {m.unread}" if m.unread else "") + (f"・別の file system {m.other_fs}（見ない）"
                                                             if m.other_fs else "")
    return f"{_size(m.size)}・ファイル {m.files}{extra}"


def _places(top: str, found: list) -> str:
    shown = [f"{top}/" if str(p) == "." else f"{top}/{p}/" for p in sorted(found)[:3]]
    return "・".join(_show(s) for s in shown) + (f" ほか {len(found) - 3}" if len(found) > 3 else "")


def scan_cache(cache: Path, known: set, out: "Out") -> None:
    """`.cache/` の直下の、 決まりの一覧に無い物と、 置かない物（git・仮想環境）を知らせる（消さない）。"""
    cst = _lstat(cache)
    try:
        with os.scandir(cache) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError as e:
        out.notice.append(f".cache/ を読めない（{_why(e)}）")
        return
    for e in entries:
        top = f".cache/{e.name}"
        st = _lstat(e.path)
        if st is None:
            continue
        if stat.S_ISLNK(st.st_mode):
            if e.name not in known:
                out.notice.append(f"{_show(top)}: 決まりの一覧に無い（symlink。 先は見ない）")
            continue
        if cst is not None and st.st_dev != cst.st_dev:
            if e.name not in known:
                out.notice.append(f"{_show(top)}: 決まりの一覧に無い（別の file system。 中は見ない）")
            continue
        m = measure(Path(e.path))
        kinds = []
        if m.git:
            kinds.append(f"git の clone・作業ツリー {_places(top, m.git)}")
        if m.venv:
            kinds.append(f"仮想環境 {_places(top, m.venv)}")
        if kinds:
            out.notice.append(f"{_show(top)}: .cache/ に置かない物（{'・'.join(kinds)}）。 {_measured(m)}")
        elif e.name not in known:
            out.notice.append(f"{_show(top)}: 決まりの一覧に無い（{_measured(m)}）")


def scan_shared(user_cache: Path, out: "Out") -> None:
    """利用者の cache の下の `mirumeai/` の直下の、 大きい物を知らせる（消さない）。"""
    shared = user_cache / "mirumeai"
    if _own_dir(shared):
        return
    try:
        with os.scandir(shared) as it:
            entries = sorted(it, key=lambda e: e.name)
    except OSError:
        return
    for e in entries:
        if e.is_symlink():
            continue
        m = measure(Path(e.path))
        if m.size >= SHARED_LARGE:
            hint = SHARED_HINT.get(e.name, "中を確かめて、 要らなければ片付ける")
            out.notice.append(f"利用者の cache の mirumeai/{_show(e.name)}（{_measured(m)}）: {hint}")


# ------------------------------------------------------------------ 消す
def blocked(top: Path) -> str:
    """作業の場所の中に、 消す途中で止まりうる物があれば、 その場所（作業の場所の直下の名前まで。 その下の
    clone の中の道には案件の名前が入りうるので出さない）と理由。 無ければ ""。

    本人が入れない dir（持ち主の読みか入りの権限が無い。 mode 000・300 など）・ほかの利用者の dir・別の file system
    （中を消すと、 ほかの置き場の物を消す）。 **消し始める前に照らし、 当たれば何も消さない**（途中まで消すと、
    中の `logs/summary.json` を失って、 次に流しても消せなくなる）。 symlink は辿らない。"""
    st = _lstat(top)
    if st is None:
        return ""
    stack = [top]
    while stack:
        d = stack.pop()
        dst = _lstat(d)
        rel = "作業の場所そのもの" if d == top else (
            f"{_show(d.relative_to(top).parts[0])}" + ("" if len(d.relative_to(top).parts) == 1 else " の中の dir"))
        if dst is None or not stat.S_ISDIR(dst.st_mode):
            continue
        if dst.st_uid != _uid():
            return f"{rel} がこの利用者の物でない"
        if dst.st_dev != st.st_dev:
            return f"{rel} が別の file system"
        if (dst.st_mode & (stat.S_IRUSR | stat.S_IXUSR)) != (stat.S_IRUSR | stat.S_IXUSR):
            return f"{rel} に本人が入れない（mode {stat.S_IMODE(dst.st_mode):03o}）"
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        stack.append(Path(e.path))
        except OSError as e:
            return f"{rel} を読めない（{_show(e.strerror or type(e).__name__)}）"
    return ""


def _rmtree(path: Path) -> None:
    """dir を消す（symlink は辿らず、 symlink そのものなら誤り）。 書けない dir（mode 555 など）の中の物は、 その dir に
    本人の権限を足して消し直す。 それ以外の誤りは、 そのまま上げる（消した物の道を出さないよう、 呼ぶ側で受ける）。"""
    top = os.fspath(path)

    def force(func, p, exc):
        err = exc if isinstance(exc, BaseException) else exc[1]      # 3.12 の onexc は例外、 3.11 までの onerror は exc_info
        p = os.fspath(p)
        parent = os.path.dirname(p)
        inside = parent == top or parent.startswith(top + os.sep)
        if func not in (os.unlink, os.remove, os.rmdir) or not isinstance(err, PermissionError) or not inside \
                or os.path.islink(parent):
            raise err                   # symlink の根（func は os.path.islink）・入れない dir（os.open）など
        os.chmod(parent, stat.S_IRWXU)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=force)
    else:
        shutil.rmtree(path, onerror=force)


def _unlink(p: Path, seen: "os.stat_result | None") -> None:
    """見たときと同じ物（この利用者の普通のファイルで、 inode が同じ）のときだけ消す。"""
    now = _lstat(p)
    if _own_file(now) or not _same(now, seen):
        raise OSError(f"{_show(p.name)} が見たときと違う物になった（消さない）")
    os.unlink(p)


def _why(e: BaseException) -> str:
    """誤りの理由（OS の誤りに付く道は出さない）。"""
    if isinstance(e, OSError):
        return _show(e.strerror or (str(e) if not e.filename else type(e).__name__))
    return type(e).__name__


def remove(r: Record, roots: list, counts: dict, where: "Where") -> str:
    """記録（と付属のファイル・作業の場所）を消す。 誤りなら理由を返す（ほかの記録は呼ぶ側が続けて扱う）。"""
    if r.work is not None:
        p, st, why = work_target(r, roots)
        if p != r.work or not _same(st, r.work_st):
            return f"作業の場所（{where(r.work)}）が見たときと違う物になった（{why or '別の dir'}）。 作業の場所も記録も残す"
        why = blocked(r.work)
        if why:
            return (f"作業の場所（{where(r.work)}）の中の {why}。 何も消さずに、 作業の場所も記録も残す"
                    "（権限を直してから流し直す）")
        try:
            _rmtree(r.work)
        except Exception as e:          # noqa: BLE001  思わぬ誤りでも止まらず、 ほかの記録を続けて扱う
            return f"作業の場所（{where(r.work)}）を消せない（{_why(e)}）。 記録も残す"
        if os.path.lexists(r.work):
            return f"作業の場所（{where(r.work)}）が消えていない。 記録も残す"
        counts["work"] += 1
    for p, st in r.companions:
        if _own_file(st):
            continue                    # 見たときに消さないと決めた物
        try:
            _unlink(p, st)
        except OSError as e:
            return f"{_show(p.name)} を消せない（{_why(e)}）"
        counts["companion"] += 1
    try:
        _unlink(r.path, r.st)
    except OSError as e:
        return f"{_show(r.name)} を消せない（{_why(e)}）"
    counts["log"] += 1
    return ""


# ------------------------------------------------------------------ 入口
class Where:
    """出力に出す道。 案件のフォルダと利用者の cache の道そのものは出さず、 そこからの相対にする。
    どちらの下でもない道は出さない。"""

    def __init__(self, case: Path, roots: list) -> None:
        self.bases = [(str(case), "案件のフォルダの ")] + [(str(r), "利用者の cache の ") for r in roots]

    def __call__(self, p) -> str:
        p = os.path.normpath(os.fspath(p))
        for base, label in self.bases:
            if p.startswith(base + os.sep):
                return label + _show(p[len(base) + 1:])
        return "決まった置き場の外の道（道は出さない。 記録の終わりの行を見る）"


@dataclass
class Out:
    where: Where
    other: list = field(default_factory=list)     # 記録の dir の、 記録でない物
    notice: list = field(default_factory=list)    # 知らせる物


def _block(title: str, lines: list) -> None:
    print(f"{title}:")
    for ln in lines or ["なし"]:
        print(f"  {ln}")


def main(argv: "list | None" = None, *, running=checks_running) -> int:
    ap = argparse.ArgumentParser(description="案件のフォルダの .cache/ と共通の道具の cache の、 使い終えた物を片付ける"
                                             "（既定は見るだけ）")
    ap.add_argument("case", help="案件のフォルダ")
    ap.add_argument("--apply", action="store_true", help="消せる物を消す（無ければ見るだけ）")
    ap.add_argument("--keep", action="append", default=[], metavar="名前",
                    help=f".cache/ の直下に置いてよい物を足す（何度でも。 既定 {', '.join(KNOWN)}）")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code else 0
    for name in a.keep:
        if not name or "/" in name or name in (".", "..") or _show(name) != name:
            print(f"引数の誤り: --keep には .cache/ の直下の名前を 1 つずつ渡してください（{_show(name)}）", file=sys.stderr)
            return 2
    case = Path(os.path.abspath(a.case))
    if not case.is_dir():
        print("案件のフォルダがありません（dir でない。 渡した道を確かめてください）", file=sys.stderr)
        return 2
    user_cache = _user_cache()
    roots = [user_cache] + ([Path(os.path.realpath(user_cache))]
                           if os.path.realpath(user_cache) != str(user_cache) else [])
    out = Out(where=Where(case, roots))
    cache = case / CACHE
    rv = cache / RV_LOGS
    print(f"===== 案件のフォルダの cache の片付け（{'--apply' if a.apply else '見るだけ'}）")
    print("利用者の cache: " + ("XDG_CACHE_HOME" if os.environ.get("XDG_CACHE_HOME") else "~/.cache")
          + "（道は出さない。 案件のフォルダの道も出さない）")

    records = []
    cst = _lstat(cache)
    if cst is None:
        out.notice.append(".cache/ がありません")
    elif stat.S_ISLNK(cst.st_mode) or not stat.S_ISDIR(cst.st_mode):
        out.notice.append(".cache/ が symlink か dir でないので、 中を見ない")
    else:
        rst = _lstat(rv)
        if rst is not None and (stat.S_ISLNK(rst.st_mode) or not stat.S_ISDIR(rst.st_mode)):
            out.notice.append(f".cache/{RV_LOGS} が symlink か dir でないので、 中を見ない")
        elif rst is not None:
            state = running()
            if state is not False:
                print("注意: この利用者の Required Verification が流れている（か分からない）ので、"
                      " 終わりの行の無い記録は残します")
            records = scan_logs(rv, GitHub(), state, roots, out)
        scan_cache(cache, set(KNOWN) | set(a.keep), out)

    gone = [r for r in records if r.delete]
    kept = [r for r in records if not r.delete]

    def label(r: Record) -> str:
        extra = [s for s in COMPANIONS if any(p.name == r.name + s and not _own_file(st) for p, st in r.companions)]
        return _show(r.name) + (f"（付属 {'・'.join(extra)}）" if extra else "")

    print()
    print(f"検査の記録（.cache/{RV_LOGS}/）: 記録 {len(records)}・消せる {len(gone)}・残す {len(kept)}")
    lines = []
    for r in gone:
        lines.append(f"{label(r)}: head {r.head[:12]}・{r.reason}")
        if r.work is not None:
            stuck = blocked(r.work)
            lines.append(f"  作業の場所（{out.where(r.work)}。 {_measured(measure(r.work))}）も消す"
                         + (f"。 ただし {stuck} ので、 --apply では何も消さずに誤りになる" if stuck else ""))
    _block("消せる物", lines)
    _block("残す物", [f"{_show(r.name)}: " + (f"head {r.head[:12]}・" if r.head else "") + r.reason for r in kept]
           + out.other)

    counts = {"log": 0, "companion": 0, "work": 0}
    errors = []
    if a.apply:
        for r in gone:
            try:
                why = remove(r, roots, counts, out.where)
            except Exception as e:      # noqa: BLE001  思わぬ誤りでも止まらず、 ほかの記録を続けて扱う
                why = f"{_show(r.name)} を扱う途中で止まった（{_why(e)}）"
            if why:
                errors.append(why)
    scan_shared(user_cache, out)
    _block("知らせる物", out.notice)
    print()
    n_work = sum(r.work is not None for r in gone)
    n_comp = sum(1 for r in gone for _, st in r.companions if not _own_file(st))
    if a.apply:
        total = sum(counts.values())
        print(f"消しました: {total} 個（記録 {counts['log']}・付属のファイル {counts['companion']}・"
              f"作業の場所 {counts['work']}）")
        for why in errors:
            print(f"消せませんでした: {why}", file=sys.stderr)
        return 1 if errors else 0
    if gone:
        print(f"--apply で消せます（記録 {len(gone)}・付属のファイル {n_comp}・作業の場所 {n_work}）")
    else:
        print("消せる物はありません")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Reviewer への頼み・Merge の門の記録・merge の後の記録・Pull Request の本文の
記録の行を、 Required Verification の記録と git・Pull Request の状態から作る。

    python3 scripts/ci/gate_record.py review <作業ツリー> --pr <番号> --issue <番号> --role a|b
        --rv-log <検査の出力> [--look <見てほしいこと>] [--human <作成者の判定>]
        [--prev <前の head> --fixed <直したこと> ...] [--out <ファイル>]
    python3 scripts/ci/gate_record.py gate <作業ツリー> --pr <番号> --rv-log <検査の出力>
        --record <JSON> [--out <ファイル>]
    python3 scripts/ci/gate_record.py merged <作業ツリー> --pr <番号> --head <40 桁>
        [--note <文>] [--out <ファイル>]
    python3 scripts/ci/gate_record.py body <作業ツリー> --pr <番号> --rv-log <検査の出力>
        --out <ファイル>

なぜ作るのか。 Pull Request ごとに、 40 桁の head・基準の main・検査の記録の行を、
頼みと記録へ手で写していた。 head が変わるたびに書き直し、 門の記録の前に main が
進んでいたのに気づかず merge したこともある（development-docs #254 の振り返り。 #105）。

**文を作るだけで、 GitHub へ書き込まない**（`gh` は `pr view` だけ）。 投稿・本文の
差し替え・merge は、 今までどおり Agent が中身を確かめてから行う。 門の条件のうち
機械で確かめられるもの（head の一致・検査の記録・main の進み・Reviewer の数・In-PR の
残り・人の判断の後の head）は、 文を作る前に確かめ、 1 つでも外れたら文を作らずに
止まる。 確かめられないもの（Reviewer の報告の中身・人の判断が要る変更かの読み・
follow-up の分け方）は、 渡されたとおりに書く。

決まりの正本は development-docs の `02_github_workflow/pull_request_review.md`
（「Reviewer を頼む」の雛形と「Merge の門」）。 雛形が変わったら、 ここも合わせる。

`gate` の `--record` は、 Reviewer の結果と follow-up を書いた JSON:

    {
      "rounds": [
        {"name": "1 回目", "head": "<commit>",
         "a": {"result": "FAIL", "blocker": 0, "major": 1, "in_pr": 1},
         "b": {"result": "PASS WITH FOLLOW-UP", "blocker": 0, "major": 0, "in_pr": 0}},
        {"name": "確かめ直し", "head": "<commit>", "a": {...}, "b": {...}}
      ],
      "summary": "直したこと（任意）",
      "skipped_reason": "飛ばした試験の理由（検査の記録の行の飛ばした試験が 0 でないときだけ。1 行）",
      "follow_up": {"in_pr": [], "post_merge": [], "external": ["..."], "backlog": ["..."]},
      "human_required": {"applies": false, "reason": "当たらない理由（1 行）",
                         "readings": {"a": false, "b": false}}
    }

`readings` は、 Reviewer A・B がそれぞれ「人の判断が要る変更に当たる」と読んだか。
作成者の `applies` と 1 つでも分かれたら止まる（人の判断に回す）。

当たるときは `"applies": true` にし、 人の言葉のまま（1 行）と日時を
`"adjudication": {"words": "...", "at": "..."}` に書く。 **言葉そのものが、 この
Pull Request（`#<番号>`・`PR <番号>` など）と今の head（12 桁以上）だけを名指ししていなければ止まる**
（番号だけの答え・古い head・別の Pull Request・別の Repository の判断は引かない）。
Agent が head を別の欄に書いて、 人が名指ししたことにする道は作らない。

人の判断が Pull Request のコメントの形（`Human Adjudication: MERGE APPROVED` と対象の head）の
ときも、 この道具の外（決まりの形で手で書く）。 言葉が番号（`#<番号>` など）を含まないので `gate` が止まる。

検査の記録の行の飛ばした試験が 0 でなければ、 理由を確かめて `skipped_reason` に書くまで止まる
（正本の「0 でなければ理由を確かめる」）。

人の確認（Human Verification）が要る Pull Request は、 この道具の外（人の確認の記録は、 人が
自分で Pull Request に書く）。 どれが要るかの正本は development-docs の
`03_customer_development/customer_isolation_check.md` の「要る場面」。 `gate` が機械で見分けて
止まるのは、 そのうち 2 つ（顧客 Repository の `customer-profile.yaml` を変える・検査が
`PROFILE EXPANSION DETECTED` を出した）だけで、 **止まらないことは、 人の確認が要らない
ことの根拠にならない**（ほかの場面に当たるかは Agent が確かめる）。 そのため `gate` は、
検査の出力にある段ごとの記録の場所を読む。 顧客 Repository で段ごとの記録が読めなければ止まる。

終了コード: 0 文を作った ／ 1 門の条件を満たさない（止まる）／ 2 流せない
（引数・ファイル・git・gh）。

顧客 Repository（下の `KNOWN` に無いもの）の文は画面に出さず、 `--out` のファイルに
書く。 止まったときの文にも、 Repository の名前と git・gh の出力を出さない。

このリポジトリは **Public**。 顧客名・社内 URL・IP アドレスをここに書かない。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import unicodedata
from pathlib import Path

RECORD_PASS = "GitHub Actions unavailable — canonical verification PASS"

#: 正本の検査の道具（`run_required_verification.py`）が出す記録の行の形
RECORD = re.compile(
    re.escape(RECORD_PASS)
    + r"（(?P<slug>[^ （）]+) head (?P<head>[0-9a-f]{12})"
    r"・基準 (?P<base_ref>[^ ]+) (?P<base>[0-9a-f]{12}) を含む: (?P<has>はい|いいえ)"
    r"・正本の道具 [^ ]+ [^ ]+ [0-9a-f]{12}・道具 [0-9a-f]{12}（(?P<same>正本と同じ|正本と違う)）・.+）$")

#: 名前を出してよい Repository（顧客の名前ではない）。 **ここに無いものは顧客 Repository として扱う**。
KNOWN = {"development-docs", ".github", ".github-private", "PDM", "Image-Acquisition-PDM"}

#: 検査の道具が出す、 段ごとの記録の場所
STEP_LOGS = re.compile(r"^段ごとの記録: (?P<dir>/.+)$", re.M)
PROFILE = "customer-profile.yaml"
EXPANSION = "PROFILE EXPANSION DETECTED"
#: main を追う ref を、 clone の refspec によらず更新する
FETCH_MAIN = ("fetch", "-q", "origin", "+refs/heads/main:refs/remotes/origin/main")

RESULTS = ("PASS", "PASS WITH FOLLOW-UP", "FAIL")
COUNTS = ("blocker", "major", "in_pr")

#: Reviewer の役割（雛形の 1 行目の名前と「見てほしいこと」）
ROLES = {
    "a": ("A", "Correctness", "Issue の要求との一致・実装の正しさ・回帰・試験・範囲・文書の整合"),
    "b": ("B", "Security / Safety", "顧客データの露出・秘密と資格情報・危ない権限・迂回の道・"
          "workflow と GitHub の安全・危ない副作用・顧客の間の混入、人の判断が要る変更に当たるか"),
}
RECHECK_LOOK = "前の所見が直ったか・変わった所に新しい問題が無いか"
TAIL = ("顧客の名前と値は、報告に写さないでください。GitHub への操作は読むだけにしてください。\n"
        "最後に、見た head、follow-up の 4 つの分類、Result と BLOCKER・MAJOR・IN-PR REQUIRED FIXES の数を出してください。\n")

#: 人の判断の印の言葉と人の確認の言葉。 人の判断の欄（adjudication）のほかに書かせない（人の確認の言葉は、 人の判断の欄にも書かせない）
MARKS = ("人の判断（人の言葉のまま）", "Human Adjudication", "Human Verification")
#: 人の言葉の中の Pull Request の番号（`#7`・`PR 7`・`PR-7`・`PR: 7`・`PR 番号 7`・`Pull Request 7`・`プルリク 7`・
#: `№7`・URL の `/pull/7`。 元の文と NFKC にそろえた文の両方で照らす）。 空白の並びは、 字をはさまずに
#: 2 つ続けない（長い空白で照らしが遅くならないように）
NUMBER = re.compile(r"(?:(?:#|№|/pull/)\s*|(?<![A-Za-z])(?:PRs?|Pull[\s-]*Requests?|プルリク(?:エスト)?|No\.)"
                    r"\s*(?:の\s*)?(?:番号\s*(?:[はが]\s*)?)?(?:[-#:]\s*)?)([0-9]+)", re.I)
#: 番号を前に書いた形（`8 番の PR`・`8 番目のプルリク`）
NUMBER_BEFORE = re.compile(r"(?<![0-9A-Za-z])([0-9]+)\s*番目?\s*(?:の\s*)?(?:PRs?|Pull[\s-]*Requests?|プルリク)"
                           r"(?![A-Za-z])", re.I)
#: 番号を前に書いた形の前に並べた番号（`7・8 番の PR`・`7 番と 8 番の PR` の 7）。 番号の前から 1 つずつたどる
#: （`$` は番号の前）。 前の番号の後の `番`（`番目`）の有無は問わない
BEFORE_MORE = re.compile(r"(?<![0-9A-Za-z])([0-9]{1,6})\s*(?:番目?\s*)?(?:[・、,/&~〜\-–—−]|と|および|及び|や|から|または|"
                         r"(?:and|or)(?![A-Za-z]))\s*$", re.I)
#: GitHub の略した書き方（`MirumeAI/<名前>#7`）。 Repository がこの Pull Request の Repository でなければ止める
SLUG_NUMBER = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\s*#\s*[0-9]", re.ASCII)
#: 番号のすぐ後に並べた番号（`#7・8`・`PR 7, 8`・`#7 と 8`・`#7 and 8`・`#7〜9`・`#7-8`・`#7 や 8`・`#7 & 8`）。
#: 7 桁以上の数字（数字だけの head の先頭）と、 すぐ後に英字が続くもの（head の先頭）は番号に数えない
MORE_NUMBER = re.compile(r"\s*(?P<sep>[・、,/&~〜\-–—−]|と|および|及び|や|から|または|(?:and|or)(?![A-Za-z]))\s*#?\s*([0-9]{1,6})"
                         r"(?![0-9A-Za-z])", re.I)
#: 区切りの後ろで、 並べた番号に数えない日付と時刻（`10/28`・`2026-10-28`・`10:30`・`10月28日` など）。
#: 月は 1〜12・日は 1〜31・時は 0〜23・分は 0〜59 のときだけ日付と時刻とみなす（`13/14` は番号に数える）。
#: 番号の前置き（`#`・`PR` など）の直後と、 `/` で続く番号（`#7/8/9`）は、 日付として扱わない
DATE_TIME = re.compile(r"(?:(?:19|20)[0-9]{2}[-/.](?P<ym>[0-9]{1,2})(?:[-/.](?P<yd>[0-9]{1,2}))?"
                       r"|(?P<m>[0-9]{1,2})/(?P<d>[0-9]{1,2})|(?P<h>[0-9]{1,2}):(?P<mi>[0-9]{2})(?::[0-9]{2})?)(?![0-9])"
                       r"|(?=[0-9]{1,4}[年月日時分])(?:[0-9]{4}年)?(?:[0-9]{1,2}月)?(?:[0-9]{1,2}日)?(?:[0-9]{1,2}時)?"
                       r"(?:[0-9]{1,2}分)?")   # 和文の日付と時刻は、 年・月・日・時・分の続きを 1 つとして飛ばす
#: 番号を範囲や続きとしてつなぐ区切り。 この後ろの数字は、 日付の形でも番号に数える（`#7/8/9`・`#7-8-9`）
CHAIN_SEPS = set("/~〜-–—−")


def _date_time_end(text: str, pos: int) -> "int | None":
    """`pos` から始まる数字が本当の日付・時刻の形なら、 その終わりの位置（区切りの後ろの数字を番号に数えないため）。"""
    m = DATE_TIME.match(text, pos)
    if not m or m.end() == pos:
        return None
    g = {k: int(v) for k, v in m.groupdict().items() if v is not None}
    month, day = g.get("ym", g.get("m")), g.get("yd", g.get("d", 1))
    if month is not None and not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    if "h" in g and not (g["h"] <= 23 and g["mi"] <= 59):
        return None
    return m.end()
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])")
#: 12 桁より短い 16 進の並び。 a〜f を含むものだけを head として照らす（数字だけの日付・時刻は除く）
SHORT_HEX = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{7,11}(?![0-9A-Za-z])")
#: 記録の行の、 飛ばした試験の件数
SKIPPED = re.compile(r"・飛ばした試験 ([0-9]+) 件・")

FOLLOW = (("in_pr", "In-PR Required Fix"), ("post_merge", "Post-Merge"),
          ("external", "External Task"), ("backlog", "Backlog"))
HEX = re.compile(r"^[0-9a-f]{7,40}$")
FULL = re.compile(r"^[0-9a-f]{40}$")
PR_URL = re.compile(r"^https://github\.com/([^/\s]+/[^/\s]+)/pull/([0-9]+)$")


class Stop(Exception):
    """門の条件を満たさない（終了コード 1）。"""


class Fatal(Exception):
    """流せない（終了コード 2）。"""


# ---------------------------------------------------------------- git と gh
def _run(args: list, cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    except OSError:
        raise Fatal(f"{args[0]} を動かせません（作業ツリーの道と、{args[0]} が入っているかを確かめてください）") from None


def _git(wt: Path, *args: str) -> str:
    r = _run(["git", *args], wt)
    if r.returncode != 0:
        raise Fatal(f"git {args[0]} が失敗しました")
    return r.stdout.strip()


def _commit(wt: Path, ref: str, what: str) -> str:
    """7〜40 桁の commit を、 作業ツリーの 40 桁に直す。"""
    if not isinstance(ref, str) or not HEX.match(ref):
        raise Fatal(f"{what} は 7〜40 桁の commit で書いてください")
    r = _run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], wt)
    if r.returncode != 0:
        raise Fatal(f"{what} の commit が作業ツリーにありません（短すぎて 1 つに決まらないときも）")
    return r.stdout.strip()


def _one_line(text, what: str, mention_ok: bool = False) -> str:
    """空でない 1 行。 印の言葉を断る（`mention_ok` なら、 記録の形〔`Human Verification:` など〕だけを断る）。"""
    if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
        raise Fatal(f"{what} は空でない 1 行の文で書いてください")
    if mention_ok:
        # 頼む文は GitHub に載せないので、 印の言葉に触れるのはよい。 記録の形（印の言葉の後に `:`）だけを断る
        if re.search(_mark_re() + r"[\W_]*?[:\-\u2010-\u2015\u2212]", _norm(text)):
            raise Fatal(f"{what} に、人の判断・人の確認の記録の形（`Human Adjudication:`・`Human Verification:` など）を"
                        "書かないでください（言葉に触れるだけなら書けます）")
        return text.strip()
    return _no_mark(text.strip(), what)


#: 外さない結合の字（かなの濁点・半濁点。 `プルリク` などの字を変えないため）
KEEP_MARKS = {"\u3099", "\u309a"}


def _bare(text: str, form: str, strip: bool = True) -> str:
    """書式の字（Cf。 ゼロ幅の空白など）を外して、 `form` で正規化する（番号・head・印の言葉を、 見えない字で割って
    隠せないように）。 `strip` なら結合の字（Mn・Me）も外す（上に重なる字で割って隠せないように）。 結合の字は、
    分けてから外す（`u` と U+0301 が `ú` の 1 字に合わさって残らないように）。"""
    t = "".join(c for c in text if unicodedata.category(c) != "Cf")
    if strip:
        t = "".join(c for c in unicodedata.normalize("NFD", t)
                    if unicodedata.category(c) not in ("Mn", "Me") or c in KEEP_MARKS)
    return unicodedata.normalize(form, t)


def _norm(text: str) -> str:
    """印の言葉を照らす形（NFKC・大文字と小文字を区別しない・空白を 1 つに）。"""
    return re.sub(r"\s+", " ", _bare(text, "NFKC")).casefold()


def _mark_re(marks=MARKS) -> str:
    """印の言葉を探す正規表現（`_norm` した文に当てる）。 印の言葉の字（文字と数字）だけを並べ、 字の間の空白・記号・
    括弧の有無を問わない（`人の判断 (人の言葉のまま)`・`Human–Verification`・`Human **Adjudication**` も印）。"""
    return "(?:" + "|".join(r"[\W_]*".join(re.escape(c) for c in _norm(m) if c.isalnum()) for m in marks) + ")"


def _no_mark(text: str, what: str) -> str:
    t = _norm(text)
    if re.search(_mark_re(("Human Verification",)), t):
        raise Fatal(f"{what} に人の確認（Human Verification）の言葉を書かないでください"
                    "（人の確認は、人が自分で Pull Request に書く記録で、門の記録に写さない）")
    if re.search(_mark_re(), t):
        raise Fatal(f"{what} に人の判断の印の言葉を書かないでください（人の判断は adjudication にだけ書く）")
    return text


class Ctx:
    """作業ツリーと Pull Request。 Pull Request は `gh pr view` で読むだけ。"""

    def __init__(self, worktree: str, number: int, fields: str, out: "str | None") -> None:
        self.wt = Path(_git(Path(worktree), "rev-parse", "--show-toplevel"))
        self.head = _git(self.wt, "rev-parse", "HEAD")
        self.number = number
        r = _run(["gh", "pr", "view", str(number), "--json", f"url,{fields}"], self.wt)
        if r.returncode != 0:
            raise Fatal("gh で Pull Request を読めません（番号・認証・作業ツリーの origin を確かめてください）")
        try:
            self.pr = json.loads(r.stdout)
        except ValueError:
            raise Fatal("gh の出力を読めません") from None
        m = PR_URL.match(str(self.pr.get("url") or ""))
        if not m or int(m.group(2)) != number:
            raise Fatal("Pull Request の URL を読めません")
        self.slug = m.group(1)
        self.masked = self.slug.split("/", 1)[1] not in KNOWN
        if self.masked and not out:
            raise Fatal("顧客 Repository の文は画面に出しません。--out でファイルに書いてください")

    def need_open(self) -> None:
        if self.pr.get("state") != "OPEN":
            raise Stop("Pull Request が開いていません")


# ---------------------------------------------------------------- 検査の記録の行
def _read_log(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise Fatal("検査の出力（--rv-log）を読めません") from None


def read_record(path: str) -> "re.Match":
    """検査の出力から、 PASS の記録の行を 1 つだけ取り出す。"""
    text = _read_log(path)
    if re.search(r"^結果: FAIL", text, re.M):
        raise Stop("検査の出力に FAIL があります（落ちた検査の出力は門に使えません）")
    lines = {ln.strip() for ln in text.splitlines() if ln.strip().startswith(RECORD_PASS)}
    if not lines:
        raise Stop("検査の出力に PASS の記録の行がありません（流せなかった検査は門に使えません）")
    if len(lines) > 1:
        raise Stop("検査の出力に違う記録の行が 2 つ以上あります（1 回分の出力を渡してください）")
    m = RECORD.match(lines.pop())
    if not m:
        raise Stop("記録の行の形が、正本の検査の道具の形と違います")
    return m


def check_record(ctx: Ctx, m: "re.Match") -> None:
    """記録の行が、 作業ツリーと Pull Request の今の head の、 使える検査であること。"""
    if ctx.pr.get("headRefOid") != ctx.head:
        raise Stop("作業ツリーの head が Pull Request の head と違います（push していない commit か、古い作業ツリー）")
    if m["slug"] != ctx.slug:
        raise Stop("記録の行の Repository が Pull Request と違います")
    if not ctx.head.startswith(m["head"]):
        raise Stop("記録の行の head が今の head と違います（今の head で検査を流し直してください）")
    if m["base_ref"] != f"origin/{ctx.pr.get('baseRefName')}":
        raise Stop("記録の行の基準が Pull Request の base と違います")
    if m["has"] != "はい":
        raise Stop("記録の行の head が基準を含みません（main を取り込んで流し直してください）")
    if m["same"] != "正本と同じ":
        raise Stop("記録の行の道具が正本と違います（正本の clone を最新にして流し直してください）")


def check_human_verification(ctx: Ctx, rv_log: str) -> None:
    """人の確認が要る Pull Request でないこと（要るものは、 この道具の外）。"""
    base = _git(ctx.wt, "merge-base", "origin/main", ctx.head)
    changed = _git(ctx.wt, "diff", "--no-renames", "--name-only", "-z", base, ctx.head).split("\0")
    if any(Path(c).name == PROFILE for c in changed if c):
        raise Stop(f"{PROFILE} を変える Pull Request は、人の確認（Human Verification）が要るので、この道具の外です")
    m = STEP_LOGS.search(_read_log(rv_log))
    logs = Path(m["dir"]) if m else None
    if logs is None or not logs.is_dir():
        if ctx.masked:
            raise Stop("検査の段ごとの記録が読めないので、PROFILE EXPANSION DETECTED が無いことを確かめられません（検査を流し直してください）")
        return              # 顧客 Repository でなければ、 混入の検査（宣言との照合）は流れない
    for f in sorted(logs.rglob("*")):
        if f.is_file() and not f.is_symlink() and EXPANSION.encode() in f.read_bytes():
            raise Stop(f"検査が {EXPANSION} を出しています。人の確認（Human Verification）が要るので、この道具の外です")


# ---------------------------------------------------------------- review
def cmd_review(a) -> "tuple[Ctx, str]":
    ctx = Ctx(a.worktree, a.pr, "state,headRefOid,baseRefName", a.out)
    ctx.need_open()
    check_record(ctx, m := read_record(a.rv_log))
    letter, title, look = ROLES[a.role]
    if (a.role == "b") != (a.human is not None):
        raise Fatal("作成者の「人の判断が要る変更」の判定（--human）は、Reviewer B にだけ必ず渡します")
    lines = []
    if a.prev:
        prev = _commit(ctx.wt, a.prev, "前の head（--prev）")
        if prev == ctx.head:
            raise Fatal("前の head が今の head と同じです（確かめ直しは head が変わったときに頼みます）")
        if not a.fixed:
            raise Fatal("確かめ直しには、直したこと（--fixed）を 1 つ以上渡してください")
        lines += [f"- 確かめ直し: 前の head {prev}。変わった所は {ctx.wt} で git diff {prev[:12]}..HEAD",
                  "- 直したこと:", *(f"  - {_one_line(x, '--fixed', True)}" for x in a.fixed)]
        look = RECHECK_LOOK + ("・人の判断が要る変更に当たるか" if a.role == "b" else "")
    elif a.fixed:
        raise Fatal("--fixed は確かめ直し（--prev）のときだけ使えます")
    if a.look is not None:
        look += "。" + _one_line(a.look, "--look", True)
    head = [f"Reviewer {letter}（{title}）として、{ctx.slug} #{ctx.number}（Issue #{a.issue}）を"
            "読み取りだけでレビューしてください。",
            f"- head: {ctx.head}",
            f"- 差分: {ctx.wt} で git diff origin/{ctx.pr['baseRefName']}...HEAD",
            f"- Required Verification: {m.group(0)}",
            f"- 見てほしいこと: {look}"]
    if a.role == "b":
        head.append(f"- 作成者の「人の判断が要る変更」の判定: {_one_line(a.human, '--human', True)}")
    return ctx, "\n".join(head + lines) + "\n" + TAIL


# ---------------------------------------------------------------- gate
def _review(x, where: str) -> dict:
    if not isinstance(x, dict) or set(x) != {"result", *COUNTS}:
        raise Fatal(f"{where} は result・blocker・major・in_pr の 4 つで書いてください")
    if x["result"] not in RESULTS:
        raise Fatal(f"{where} の result は {' / '.join(RESULTS)} のどれかです（略さない）")
    for k in COUNTS:
        if type(x[k]) is not int or x[k] < 0:
            raise Fatal(f"{where} の {k} は 0 以上の整数です")
    # BLOCKER と MAJOR は必ず In-PR に数える。 FAIL は In-PR が 1 以上（正本の「Reviewer の出力」）
    if x["in_pr"] < x["blocker"] + x["major"]:
        raise Fatal(f"{where} の in_pr が BLOCKER と MAJOR の数より少ない（報告を写し直してください）")
    if (x["result"] == "FAIL") != (x["in_pr"] > 0):
        raise Fatal(f"{where} の result と in_pr が合いません（FAIL は in_pr が 1 以上）")
    return x


def load_record(path: str, wt: Path) -> dict:
    try:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise Fatal("--record の JSON を読めません") from None
    if not isinstance(rec, dict) or not set(rec) <= {"rounds", "summary", "skipped_reason", "follow_up",
                                                     "human_required"} \
            or not {"rounds", "human_required"} <= set(rec):
        raise Fatal("--record は rounds・human_required（と任意の summary・skipped_reason・follow_up）で書いてください")
    rounds = rec["rounds"]
    if not isinstance(rounds, list) or not rounds:
        raise Fatal("rounds に Reviewer の結果を 1 回以上書いてください")
    for i, r in enumerate(rounds):
        where = f"rounds[{i}]"
        if not isinstance(r, dict) or not set(r) <= {"name", "head", "a", "b"} \
                or not {"name", "head"} <= set(r) or not ({"a", "b"} & set(r)):
            raise Fatal(f"{where} は name・head と、a・b の少なくとも 1 つで書いてください")
        if "|" in _one_line(r["name"], f"{where}.name"):
            raise Fatal(f"{where}.name に | を使わないでください")
        r["head"] = _commit(wt, r["head"], f"{where}.head")
        for role in ("a", "b"):
            if role in r:
                _review(r[role], f"{where}.{role}")
    summary = rec.get("summary", "")
    if not isinstance(summary, str):
        raise Fatal("summary は文で書いてください")
    _no_mark(summary, "summary")
    if "skipped_reason" in rec:
        rec["skipped_reason"] = _one_line(rec["skipped_reason"], "skipped_reason")
    follow = rec.get("follow_up", {})
    if not isinstance(follow, dict) or not set(follow) <= {k for k, _ in FOLLOW}:
        raise Fatal("follow_up は in_pr・post_merge・external・backlog で書いてください")
    for k, v in follow.items():
        if not isinstance(v, list):
            raise Fatal(f"follow_up.{k} は文の list で書いてください")
        follow[k] = [_one_line(x, f"follow_up.{k}") for x in v]
    hr = rec["human_required"]
    if not isinstance(hr, dict) or type(hr.get("applies")) is not bool:
        raise Fatal("human_required.applies は true か false で書いてください")
    if set(hr) != {"applies", "reason", "readings", *(("adjudication",) if hr["applies"] else ())}:
        raise Fatal("human_required は applies・reason・readings（当たるときは adjudication も）で書いてください")
    hr["reason"] = _one_line(hr["reason"], "human_required.reason")
    rd = hr["readings"]
    if not isinstance(rd, dict) or set(rd) != {"a", "b"} or any(type(v) is not bool for v in rd.values()):
        raise Fatal("human_required.readings は a・b（Reviewer の読み。true か false）で書いてください")
    if hr["applies"]:
        adj = hr["adjudication"]
        if not isinstance(adj, dict) or set(adj) != {"words", "at"}:
            raise Fatal("adjudication は words（人の言葉のまま。1 行）と at（日時）で書いてください")
        for k in ("words", "at"):
            if not isinstance(adj[k], str) or not adj[k].strip() or "\n" in adj[k] or "\r" in adj[k]:
                raise Fatal(f"adjudication.{k} は空でない 1 行の文で書いてください")
    return rec


def named_head(ctx: Ctx, words: str) -> str:
    """人の言葉が、 この Pull Request と今の head だけを名指ししていること。 名指しした head を返す。

    元の文と、 全角を NFKC で半角にそろえた文の **両方** で（それぞれ結合の字を外した文と外さない文で）、
    番号・head・Repository の名前を探して合わせる（そろえた文だけで探すと、 全角の字が隣に来て見えなくなる並びがある）。 16 進と
    Repository の名前は大文字と小文字を区別しない。"""
    if re.search(_mark_re(("Human Verification",)), _norm(words)):
        raise Stop("人の判断の言葉に、人の確認（Human Verification）の言葉があります（人の確認は、人が自分で Pull Request に書く記録で、この道具の外）")
    if re.search(_mark_re(), _norm(words)):
        raise Stop("人の判断の言葉が Pull Request のコメントの形（Human Adjudication の印）です。この道具の外なので、決まりの形で手で書く")
    # 書式の字（Cf）は外してから照らす。 結合の字（Mn・Me）は、 外した文と外さない文の両方で照らす（外すと、 上に
    # 重ねて番号や head を割る形が見える。 外さないと、 `ö` のような字が head や名前の前の境のまま残る）
    texts = tuple(dict.fromkeys(_bare(words, form, strip) for form in ("NFC", "NFKC") for strip in (False, True)))
    nums = set()
    for t in texts:
        for m in NUMBER_BEFORE.finditer(t):
            nums.add(int(m[1]))
            end = m.start(1)
            while (more := BEFORE_MORE.search(t, 0, end)):          # 前に並べた番号（`8・7 番の PR` の 8）
                nums.add(int(more[1]))
                end = more.start(1)
        for m in NUMBER.finditer(t):
            nums.add(int(m[1]))
            pos = m.end()
            while (more := MORE_NUMBER.match(t, pos)):
                # 区切りの後ろの日付・時刻（`#7・10/28`）は番号に数えず、 飛ばして続きを見る（`#7・10/28・9` の 9 は数える）。
                # つなぐ区切りで続く番号（`#7/8/9`）は日付にしない
                end = _date_time_end(t, more.start(2)) if more["sep"] not in CHAIN_SEPS else None
                if end is None:
                    nums.add(int(more[2]))
                    end = more.end()
                pos = end
    # URL の番号は、 URL の Repository がこの Pull Request の Repository と同じときだけ（別の Repository の判断は引かない）
    for t in texts:
        for owner_repo in re.findall(r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/[0-9]+", t, re.I):   # host の無い形も
            if owner_repo.casefold() != ctx.slug.casefold():
                raise Stop("人の判断の言葉が、別の Repository を名指ししています（URL の Repository が違います）")
    # 略した書き方（`<owner>/<名前>#7`）と、 この Pull Request の owner の下の名前（`MirumeAI/<名前>`）も同じ
    owner, repo = ctx.slug.split("/", 1)
    for t in texts:
        # 数字だけの owner と名前（`10/28 #7` の日付）は、 Repository と読まない
        named = [s for s in SLUG_NUMBER.findall(t) if not re.fullmatch(r"[0-9._-]+/[0-9._-]+", s)] + [
            f"{owner}/{n}" for n in re.findall(
            rf"(?<![\w.-]){re.escape(owner)}/([A-Za-z0-9_.-]+)", t, re.ASCII | re.I)]
        if any(s.rstrip(".").casefold() != ctx.slug.casefold() for s in named):
            raise Stop("人の判断の言葉が、別の Repository を名指ししています（`owner/名前` の Repository が違います）")
    if nums != {ctx.number}:
        raise Stop("人の判断の言葉が、この Pull Request（#番号）だけを名指ししていません（番号だけの答え・別の Pull Request の判断は引かない。"
                   "Pull Request のコメントの形の人の判断は、この道具の外なので決まりの形で手で書く）")
    # 区切りは ASCII で見る（Unicode の \w だと、 名前のすぐ後に和文が続くと見逃す）
    if any(re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", t, re.ASCII | re.I) for n in KNOWN - {repo} for t in texts):
        raise Stop("人の判断の言葉が、別の Repository を名指ししています")
    heads = sorted({h for t in texts for h in HEX_TOKEN.findall(t.lower())})
    if not heads:
        raise Stop("人の判断の言葉に head（12 桁以上）がありません")
    if any(not ctx.head.startswith(h) for h in heads):
        raise Stop("人の判断の言葉が名指しした head が、merge する head と違います（判断の後に head が変わったら、人の判断を取り直してください）")
    if any(re.search("[a-f]", s) and not ctx.head.startswith(s) for t in texts for s in SHORT_HEX.findall(t.lower())):
        raise Stop("人の判断の言葉に、merge する head の先頭と違う短い head（7〜11 桁）があります（別の head を並べた言葉は引かない）")
    return max(heads, key=len)


def _cell(x: "dict | None") -> str:
    if x is None:
        return "—"
    return f"{x['result']}（BLOCKER {x['blocker']}・MAJOR {x['major']}・IN-PR {x['in_pr']}）"


def cmd_gate(a) -> "tuple[Ctx, str, str]":
    ctx = Ctx(a.worktree, a.pr, "state,headRefOid,baseRefName,isDraft", a.out)
    ctx.need_open()
    if ctx.pr.get("baseRefName") != "main":
        raise Stop("base が main でない Pull Request（LEGACY の長期 Branch など）は、この道具の外です（決まりの形で手で書く）")
    check_record(ctx, m := read_record(a.rv_log))
    rec = load_record(a.record, ctx.wt)
    sk = SKIPPED.search(m.group(0))
    if sk is None:
        raise Stop("記録の行に飛ばした試験の件数がありません（正本の検査の道具の記録の行を渡してください）")
    skipped = int(sk[1])
    if skipped and "skipped_reason" not in rec:
        raise Stop(f"検査で飛ばした試験が {skipped} 件あります。理由を確かめて、--record の skipped_reason に書いてください"
                   "（正本は「0 でなければ理由を確かめる」）")
    if not skipped and "skipped_reason" in rec:
        raise Fatal("飛ばした試験が 0 件なのに、skipped_reason があります")

    # main が検査の基準から進んでいないか（進んでいたら、 取り込み・流し直し・確かめ直し）
    if _run(["git", *FETCH_MAIN], ctx.wt).returncode != 0:
        raise Fatal("origin の main を fetch できません")
    main = _git(ctx.wt, "rev-parse", "origin/main")
    if not main.startswith(m["base"]):
        raise Stop("main が検査の基準から進みました（main を取り込み、検査を流し直し、Reviewer に確かめ直してもらってください）")
    if _run(["git", "merge-base", "--is-ancestor", main, ctx.head], ctx.wt).returncode != 0:
        raise Stop("head が main を含みません")
    check_human_verification(ctx, a.rv_log)

    # Reviewer ごとの最後の結果が、 merge する head を見て 0・0・0 であること
    last = {}
    for r in rec["rounds"]:
        for role in ("a", "b"):
            if role in r:
                last[role] = (r["head"], r[role])
    for role in ("a", "b"):
        letter = ROLES[role][0]
        if role not in last:
            raise Stop(f"Reviewer {letter} の結果がありません")
        seen, x = last[role]
        if seen != ctx.head:
            raise Stop(f"Reviewer {letter} の最後の結果が、merge する head を見ていません（確かめ直してもらってください）")
        if any(x[k] for k in COUNTS):
            raise Stop(f"Reviewer {letter} の BLOCKER・MAJOR・IN-PR が 0 ではありません（直して確かめ直してもらってください）")
    follow = rec.get("follow_up", {})
    if follow.get("in_pr"):
        raise Stop("In-PR の直しが残っています")
    hr = rec["human_required"]
    if any(v != hr["applies"] for v in hr["readings"].values()):
        raise Stop("人の判断が要る変更かの読みが、作成者と Reviewer で分かれています（人の判断に回してください）")
    named = named_head(ctx, hr["adjudication"]["words"]) if hr["applies"] else ""

    out = ["## Merge の門の記録（AUTO MERGE Gate）", "",
           f"- merge する head: `{ctx.head}`（基準 origin/main `{m['base']}` を含む）",
           f"- Required Verification: {m.group(0)}",
           *([f"- 飛ばした試験 {skipped} 件の理由（Agent が確かめた）: {rec['skipped_reason']}"] if skipped else []), "",
           "### Reviewer", "",
           "| 回 | 見た head | Reviewer A（Correctness） | Reviewer B（Security / Safety） |",
           "|---|---|---|---|"]
    out += [f"| {r['name']} | {r['head'][:12]} | {_cell(r.get('a'))} | {_cell(r.get('b'))} |" for r in rec["rounds"]]
    if rec.get("summary", "").strip():
        out += ["", rec["summary"].strip()]
    out += ["", "### follow-up", ""]
    out += [f"- {label}: {'・'.join(follow.get(k, [])) or 'なし'}" for k, label in FOLLOW]
    out += ["", "### 人の判断が要る変更か", ""]
    if hr["applies"]:
        adj = hr["adjudication"]
        out += [f"当たる（作成者・A・B の読みが同じ）。{hr['reason']}", "",
                f"人の判断（人の言葉のまま）: 「{adj['words'].strip()}」（{adj['at'].strip()}）。"
                f"言葉が名指しした Pull Request #{ctx.number} と head `{named}` は、merge する head と同じ"
                "（判断の後に head は変わっていない）。"]
    else:
        out += [f"当たらない（作成者・A・B の読みが同じ）。{hr['reason']}"]
    out += ["", "道具が確かめた条件（head の一致・検査の記録・main の進み・Reviewer A・B の最後の結果・In-PR の残り・"
            "人の判断が要る変更の読み）は満たす。道具が見ない条件（development-docs の "
            "`02_github_workflow/pull_request_review.md` の「Merge の門」の一覧。follow-up の判断の中身・"
            "Product Release の手順の間でないか・人の言葉が人自身のものか・渡した記録が報告と人の言葉に合うかなど）と、人の確認が要る場面"
            "（development-docs の `03_customer_development/customer_isolation_check.md` の「要る場面」）に当たらないことを"
            "Agent が確かめてから、head を固定して Squash and merge する（人の確認が要る Pull Request は、この道具の外。"
            "人の確認そのものは人が書く）。"]

    ready = f"gh pr ready {ctx.number} && " if ctx.pr.get("isDraft") else ""
    cmd = (f"cd {shlex.quote(str(ctx.wt))} && git {' '.join(FETCH_MAIN)}"
           f" && [ \"$(git rev-parse origin/main)\" = \"{main}\" ]"
           f" && {ready}gh pr merge {ctx.number} --squash --match-head-commit {ctx.head}")
    return ctx, "\n".join(out) + "\n", cmd


# ---------------------------------------------------------------- merged・body
def cmd_merged(a) -> "tuple[Ctx, str]":
    if not FULL.match(a.head):
        raise Fatal("--head は固定した head の 40 桁で書いてください")
    ctx = Ctx(a.worktree, a.pr, "state,headRefOid,mergeCommit", a.out)
    if ctx.pr.get("state") != "MERGED":
        raise Stop("Pull Request はまだ merge されていません")
    if ctx.pr.get("headRefOid") != a.head:
        raise Stop("merge された head が、固定した head と違います")
    oid = (ctx.pr.get("mergeCommit") or {}).get("oid") or ""
    if not FULL.match(oid):
        raise Fatal("merge commit を読めません")
    note = _one_line(a.note, "--note") if a.note is not None else ""
    return ctx, f"merge しました（Squash、merge commit `{oid}`、固定した head `{a.head}`）。{note}\n"


def cmd_body(a) -> "tuple[Ctx, str]":
    ctx = Ctx(a.worktree, a.pr, "state,headRefOid,baseRefName,body", a.out)
    ctx.need_open()
    check_record(ctx, m := read_record(a.rv_log))
    parts = str(ctx.pr.get("body") or "").splitlines(keepends=True)
    idx = [i for i, ln in enumerate(parts) if ln.strip().startswith(RECORD_PASS)]
    if len(idx) != 1:
        raise Stop(f"本文の記録の行が {len(idx)} 個です（1 つのときだけ差し替えます。無いときは手で足す）")
    old = parts[idx[0]]
    parts[idx[0]] = m.group(0) + old[len(old.rstrip("\r\n")):]
    return ctx, "".join(parts)


# ---------------------------------------------------------------- 出力
def _write(path: str, text: str) -> None:
    """本人だけが読める形（0600）で書く。 link は辿らない。 既にあるファイルも 0600 にする。"""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
    except OSError:
        raise Fatal("--out のファイルに書けません") from None


def emit(ctx: Ctx, text: str, out: "str | None") -> None:
    if not out:
        sys.stdout.write(text)
        return
    _write(out, text)
    print(f"書きました: {out}")


def main(argv: "list | None" = None) -> int:
    ap = argparse.ArgumentParser(description="Reviewer への頼み・門の記録・merge の後の記録・本文の記録の行を作る（GitHub へは書き込まない）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, rv: bool) -> None:
        p.add_argument("worktree", help="Pull Request の head の作業ツリー")
        p.add_argument("--pr", type=int, required=True, help="Pull Request の番号")
        if rv:
            p.add_argument("--rv-log", required=True, help="正本の検査の道具の出力を書いたファイル")

    p = sub.add_parser("review", help="Reviewer A・B への頼み（初めと確かめ直し）")
    common(p, True)
    p.add_argument("--issue", type=int, required=True, help="Issue の番号")
    p.add_argument("--role", choices=("a", "b"), required=True)
    p.add_argument("--look", help="見てほしいことに足す文（1 行）")
    p.add_argument("--human", help="作成者の「人の判断が要る変更」の判定（B では必ず）")
    p.add_argument("--prev", help="確かめ直しのときの前の head")
    p.add_argument("--fixed", action="append", default=[], help="確かめ直しのときの直したこと（1 行ずつ。何度でも）")
    p.add_argument("--out", help="文を書くファイル（顧客 Repository では必ず）")
    p = sub.add_parser("gate", help="門の記録と、head を固定した merge の命令")
    common(p, True)
    p.add_argument("--record", required=True, help="Reviewer の結果と follow-up の JSON")
    p.add_argument("--out", help="門の記録を書くファイル（顧客 Repository では必ず）")
    p = sub.add_parser("merged", help="merge の後のコメント")
    common(p, False)
    p.add_argument("--head", required=True, help="固定した head（40 桁）")
    p.add_argument("--note", help="後に足す文（1 行）")
    p.add_argument("--out", help="文を書くファイル（顧客 Repository では必ず）")
    p = sub.add_parser("body", help="本文の記録の行を、今の検査の記録の行に差し替えた本文")
    common(p, True)
    p.add_argument("--out", required=True, help="差し替えた本文を書くファイル")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "gate":
            ctx, text, cmd = cmd_gate(a)
            emit(ctx, text, a.out)
            if ctx.masked:
                # 顧客 Repository では、 作業ツリーの道も画面に出さない
                _write(a.out + ".merge", cmd + "\n")
                print(f"merge の命令は {a.out}.merge に書きました（門の記録を投稿してから流す。main が進んでいたら止まる）",
                      file=sys.stderr)
            else:
                print("merge の命令（門の記録を投稿してから流す。main が進んでいたら止まる）:", file=sys.stderr)
                print(cmd, file=sys.stderr)
        else:
            ctx, text = {"review": cmd_review, "merged": cmd_merged, "body": cmd_body}[a.cmd](a)
            emit(ctx, text, a.out)
    except Stop as e:
        print(f"止まりました: {e}", file=sys.stderr)
        return 1
    except Fatal as e:
        print(f"流せません: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

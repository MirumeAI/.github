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
      "follow_up": {"in_pr": [], "post_merge": [], "external": ["..."], "backlog": ["..."]},
      "human_required": {"applies": false, "reason": "当たらない理由（1 行）",
                         "readings": {"a": false, "b": false}}
    }

`readings` は、 Reviewer A・B がそれぞれ「人の判断が要る変更に当たる」と読んだか。
作成者の `applies` と 1 つでも分かれたら止まる（人の判断に回す）。

当たるときは `"applies": true` にし、 人の言葉のまま（1 行）と日時を
`"adjudication": {"words": "...", "at": "..."}` に書く。 **言葉そのものが、 この
Pull Request（`#<番号>`）と今の head（12 桁以上）だけを名指ししていなければ止まる**
（番号だけの答え・古い head・別の Pull Request・別の Repository の判断は引かない）。
Agent が head を別の欄に書いて、 人が名指ししたことにする道は作らない。

人の確認（Human Verification）が要る Pull Request（顧客 Repository の `customer-profile.yaml`
を変える・検査が `PROFILE EXPANSION DETECTED` を出した）は、 この道具の外として `gate` が
止まる（人の確認の記録は、 人が自分で Pull Request に書く）。 そのため `gate` は、 検査の
出力にある段ごとの記録の場所を読む。 顧客 Repository で段ごとの記録が読めなければ止まる。

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

#: 人の判断の印の言葉。 人の判断の欄（adjudication）のほかに書かせない
MARKS = ("人の判断（人の言葉のまま）", "Human Adjudication")
NUMBER = re.compile(r"[#＃]\s*([0-9]+)")
HEX_TOKEN = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{12,40}(?![0-9A-Za-z])")

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


def _one_line(text, what: str) -> str:
    if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
        raise Fatal(f"{what} は空でない 1 行の文で書いてください")
    return _no_mark(text.strip(), what)


def _no_mark(text: str, what: str) -> str:
    if any(mark in text for mark in MARKS):
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
                  "- 直したこと:", *(f"  - {_one_line(x, '--fixed')}" for x in a.fixed)]
        look = RECHECK_LOOK + ("・人の判断が要る変更に当たるか" if a.role == "b" else "")
    elif a.fixed:
        raise Fatal("--fixed は確かめ直し（--prev）のときだけ使えます")
    if a.look is not None:
        look += "。" + _one_line(a.look, "--look")
    head = [f"Reviewer {letter}（{title}）として、{ctx.slug} #{ctx.number}（Issue #{a.issue}）を"
            "読み取りだけでレビューしてください。",
            f"- head: {ctx.head}",
            f"- 差分: {ctx.wt} で git diff origin/{ctx.pr['baseRefName']}...HEAD",
            f"- Required Verification: {m.group(0)}",
            f"- 見てほしいこと: {look}"]
    if a.role == "b":
        head.append(f"- 作成者の「人の判断が要る変更」の判定: {_one_line(a.human, '--human')}")
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
    if not isinstance(rec, dict) or not set(rec) <= {"rounds", "summary", "follow_up", "human_required"} \
            or not {"rounds", "human_required"} <= set(rec):
        raise Fatal("--record は rounds・human_required（と任意の summary・follow_up）で書いてください")
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
    """人の言葉が、 この Pull Request と今の head だけを名指ししていること。 名指しした head を返す。"""
    if {int(n) for n in NUMBER.findall(words)} != {ctx.number}:
        raise Stop("人の判断の言葉が、この Pull Request（#番号）だけを名指ししていません（番号だけの答え・別の Pull Request の判断は引かない）")
    repo = ctx.slug.split("/", 1)[1]
    if any(re.search(rf"(?<![\w.-]){re.escape(n)}(?![\w-])", words) for n in KNOWN - {repo}):
        raise Stop("人の判断の言葉が、別の Repository を名指ししています")
    heads = HEX_TOKEN.findall(words)
    if not heads:
        raise Stop("人の判断の言葉に head（12 桁以上）がありません")
    if any(not ctx.head.startswith(h) for h in heads):
        raise Stop("人の判断の言葉が名指しした head が、merge する head と違います（判断の後に head が変わったら、人の判断を取り直してください）")
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
           f"- Required Verification: {m.group(0)}", "",
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
    out += ["", "以上で門の条件を満たすので、Agent が head を固定して Squash and merge する。"]

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

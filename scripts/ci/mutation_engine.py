#!/usr/bin/env python3
"""変異の確かめの仕組み。 テストが本当に検証しているかを、 守りを壊して確かめる。

**通っているテストが何も確かめていないことがある。** PDM では、 13 件すべて合格していた画面の
試験が、 構文エラーで丸ごと死んでいた script を見ていなかった。 撮像側では、 名前の検査を丸ごと
外しても試験が 6/6 合格していた（別の守りが 400 を返すため）。 ここでは「守りを 1 つ壊したら
試験が落ちること」を確かめる。 落ちなければ、 その試験はその守りを検証していない。

**この file は写し。** 正本は `MirumeAI/.github` の `scripts/ci/mutation_engine.py`。 各 Core で
直さず、 正本を直して同期の Pull Request で合わせる（写しの照合 `copy_sync_check.py` の `SHARED`。
MirumeAI/.github#206）。 前は各 Core が `scripts/ci/mutation_check.py` に同じ仕組みを手で写して持ち、
直しが片方にだけ入るずれが起きえた。

各 Core の `scripts/ci/mutation_check.py` には、 Core に固有のもの（変異の表 `MUTATIONS`・括弧の
確かめの読み方 `Brackets`・写しの予備の除外）と入口だけを置く:

    TABLE = mutation_engine.Table(rows=(*MUTATIONS, *mutation_engine.ENGINE_MUTATIONS), root=ROOT,
                                  seconds=HERE / "mutation_seconds.json", brackets=..., fallback_ignore=...)
    sys.exit(mutation_engine.main(sys.argv[1:], TABLE))

使い方（各 Core で）:
    python scripts/ci/mutation_check.py               # 表の全部
    python scripts/ci/mutation_check.py --part K/N    # 表を N に分けた K 番目（CI は job ごとに分けて流す）
    python scripts/ci/mutation_engine.py seconds [--out <道>] <変異の job の記録>...   # 秒の表を作り直す

表の行は `(説明, 対象ファイル, 置換前, 置換後, 落ちるべき検査)`。 検査は `python -m unittest` の名前
（空白区切りで複数書ける）か、 `script:<根からの道>`（`.sh` は bash、 ほかは python で流す）。

  ・置換前が見つからない・対象ファイルが無い行は**失敗にする**（コードを直したときに表が古びたまま
    「壊せていないのに通った」とならないように）
  ・当てると読めなくなる置換後（Python の文法の誤り・`bash -n` が通らない・JSON や YAML として
    読めない・画面の script と `.js` の括弧の崩れ・systemd の単位の崩れ）は、 壊して流す前に表の
    誤りとして止める。 壊したいことを保ったまま、 読める形（空にするなら `pass` など）で書く
  ・試験を読み込めずに落ちた（読み込むときの NameError・ImportError・SyntaxError。 試験が始まった後に
    読み込むモジュールの本体のものも）行と、 試験の流し手が結果を書けずに止まった行は、 守りを
    確かめていないので失敗にする（MirumeAI/.github#206 の 4-a）
  ・括弧の対応そのものを確かめる試験と組んだ行は、 わざと括弧を崩すので括弧を見ない。 外すのは、
    行の試験が**すべて**括弧の確かめの試験のときだけ（同じ #206 の 5-a）

リポジトリは変更しない。 一時フォルダへ写して壊す。 行ごとにかかった秒と、 終わりに合計と遅い順の
上位 10 件を出す（出すだけで、 合否は変えない）。

このリポジトリは **Public**。 顧客名・社内 URL・IP アドレスをここに書かない。
"""
from __future__ import annotations

import importlib.util
import json
import math
import os
import re
import shlex
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import warnings
from pathlib import Path
from typing import Callable, NamedTuple

HERE = Path(__file__).resolve().parent

#: 秒の表の名前（各 Core の `scripts/ci/` に、 この file と並べて置く）
SECONDS_NAME = "mutation_seconds.json"

#: この file の、 Core の根からの道。 仕組み自身を壊す行（`ENGINE_MUTATIONS`）が壊す先
ENGINE_FILE = "scripts/ci/mutation_engine.py"

#: 仕組みの試験（共通の写し `scripts/ci/test_mutation_engine.py`）の名前。 `ENGINE_MUTATIONS` の行が照らす
ENGINE_TESTS = "scripts.ci.test_mutation_engine"


class Brackets(NamedTuple):
    """画面の script と `.js` の括弧の確かめ（Core の画面の試験が持つ字句の確かめを借りる）。

    変異の job には JS を動かすものが無いので、 構文全部は見ず、 括弧の対応だけを見る。"""

    #: 確かめを持つ試験の file（根からの道）。 写しに無ければ括弧を見ない（`syntax_notes` が言う）
    where: str
    #: 括弧の対応そのものを確かめる試験の**クラス**（`tests.a.test_b.Class`）。 行の試験が**すべて**これ
    #: （かその中の試験）のときだけ、 その行はわざと括弧を崩す（崩れたのを捕まえるかを見る）ので括弧を
    #: 見ない（#206 の 5-a）。 モジュールの単位にすると、 同じモジュールのほかの試験と組んだ行まで外れる
    tests: tuple
    #: (読んだ試験のモジュール, JS の文) → 対応していない括弧の文。 対応していれば None
    js: Callable
    #: 読んだ試験のモジュール → 画面の中の script（`src=` の無いもの）を拾う正規表現（group 1 が中身）
    scripts: Callable


class Table(NamedTuple):
    """Core に固有のもの。 各 Core の `mutation_check.py` が作って `main` に渡す（試験は `_replace` で行を替える）。"""

    #: 変異の表。 Core の行（`MUTATIONS`）と、 仕組み自身を壊す行（`ENGINE_MUTATIONS`）
    rows: tuple
    #: Core の clone の根（写しを作る元）
    root: Path
    #: 秒の表（`SECONDS_NAME`。 表を時間でそろえて分けるためだけに使う）
    seconds: Path
    #: 括弧の確かめ。 None なら画面の script と `.js` を見ない（`syntax_notes` が言う）
    brackets: "Brackets | None" = None
    #: git で一覧を読めないときに写さないもの（`shutil.ignore_patterns` の形）
    fallback_ignore: tuple = (".git", ".venv", "__pycache__", "*.pyc")


# ---------------------------------------------------------------- 検査を流す
def run(tmp: Path, check: str) -> "tuple[bool, str]":
    """`(その検査が通ったか, 出力の末尾)`。 tmp（写し）を作業の場所にして流す。

    **通ってしまったときに何が起きたのかを出す**ため、 出力の末尾を返す（手元で再現しない環境
    〔CI でだけ通る〕では、 これが無いと追いようがない）。
    """
    # 検査ごとの一時のフォルダ。 流し手の結果のファイルと、 検査に渡す作業ルート（下の env）を置き、 終わったら消す
    scratch = Path(tempfile.mkdtemp(prefix="mirume_mut_run_"))
    result_file = None
    if check.startswith("script:"):
        # 守りが `.sh` の中にあることもある（据え付けの script）。 `.sh` は bash、 ほかの道具は python で流す
        rel = check.split(":", 1)[1]
        cmd = ["bash", rel] if rel.endswith(".sh") else [sys.executable, rel]
    else:
        # `python -m unittest` と同じに読み込んで流し、 試験の名前ごとの結果を、 出力の文でなく別のファイルに
        # 書かせる（子の unittest の出力を assert の文に入れる試験や、 名前を並べた行で 1 つだけ読み込めない
        # ときに取り違えないように）。 空白区切りで複数指定できる（試験同士の混ざり方は 1 つずつでは再現しない）
        result_file = scratch / "result.json"
        cmd = [sys.executable, "-c", RUNNER, str(result_file), *check.split()]
    # **作業ルートとデータの起点は、 検査ごとの一時のフォルダを渡す。** 流す人の環境の値（開発の機体の本物の
    # 置き場）は使わない。 渡さないと、 作業ルートが写し（コードの場所）に戻る。 試験のパッケージの入口は、 印
    # （`WORK_MARK`）が作業ルートと同じなら、 それを使う。 `script:` の行にも同じ値が渡る。 検査ごとに新しくするので、
    # 前の変異の記録が残らない
    work = scratch / "work"
    work.mkdir()
    # **バイトコード（.pyc）を書かせない。** 変異は同じ写しの中で次々に入れては戻すので、 前の実行が書いた .pyc が
    # 残る。 Python は .pyc を「元の大きさ」と「秒単位の更新時刻」で確かめるので、 同じ大きさの書き換えを同じ 1 秒の
    # 中で入れると、 前の中身の .pyc を使って変異が効かず、 「通ってしまった」と出た（両 Core で実際に起きた）
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    env.update({WORK_MARK: str(work), "MIRUME_WORK_ROOT": str(work), "MIRUME_DATA_ROOT": str(work / "data")})
    try:
        r = subprocess.run(cmd, cwd=str(tmp), capture_output=True, text=True, env=env)
        result = _read_result(result_file) if result_file is not None else None
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    tail = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-6:]
    # **試験を読み込めずに落ちたのか**を、 先頭の印で知らせる。 そのときは守りを確かめたことにならない。 `script:` の
    # 行は試験の名前ごとの結果が無いので見分けない（script の中で読み込めずに落ちても「落ちた」と数える。 表に書く人と
    # Reviewer が確かめる）
    if r.returncode != 0 and result_file is not None and _load_failed(result):
        tail = [LOAD_FAILED] + tail
    return r.returncode == 0, "\n".join(tail)


#: `run()` の出力の末尾の先頭に付ける、 試験を読み込めずに落ちた印
LOAD_FAILED = "[試験を読み込めずに落ちた]"

#: 検査に渡す一時の作業ルートの印（撮像側の試験のパッケージの入口 `tests/*/__init__.py` が読む）。 作業ルートと同じ値なら、
#: 入口はそれを使う（同じでなければ、 自分で一時のフォルダを作る）。 使わない Core では、 ただの環境変数
WORK_MARK = "MIRUME_TESTS_WORK_ROOT"

#: 試験を流す小さな流し手。 `python -m unittest <名前>…` と同じに読み込んで流し、 試験の名前ごとの結果を、
#: 最初の引数のファイルに JSON で書く。
#:   load_failed: 読み込めなかった名前（ImportError は unittest が `_FailedTest` にする。 ほかの誤りは
#:                `python -m unittest` なら試験を 1 つも流さずに止まる）
#:   in_import:   試験が始まった後の読み込みの誤りで落ちた試験（setUp・subTest などで読み込むモジュールの本体を
#:                流している途中〔traceback に `<module>` の枠がある。 本体から呼んだ関数やクラスの本体の中も〕の
#:                NameError・ImportError・SyntaxError。 当てたモジュール自身の文法の誤りは、 前の段の文法の確かめで止まる）
#:   failed:      落ちた試験（in_import を含む。 読み込めなかった名前の代わりの `_FailedTest` は含まない）
RUNNER = r'''
import json, os, sys, traceback, unittest

# `python -m unittest` と同じく、 作業の場所を絶対の道で読む（`-c` は '' を置くので、 試験の中で作業の場所を
# 変えると、 まだ読んでいないモジュールを読めなくなる）
if sys.path and sys.path[0] == "":
    sys.path[0] = os.getcwd()
out, names = sys.argv[1], sys.argv[2:]
IMPORT_ERRORS = (NameError, ImportError, SyntaxError)


class Result(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.in_import = []

    def addError(self, test, err):
        super().addError(test, err)
        self.note(test, err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.note(subtest, err)

    def note(self, test, err):
        # 試験の traceback は試験の枠から始まるので、 途中の `<module>` の枠は、 モジュールの本体を流している途中を指す
        tb, frames = err[2], []
        while tb is not None:
            frames.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        if issubclass(err[0], IMPORT_ERRORS) and "<module>" in frames:
            self.in_import.append(test.id())


loader = unittest.TestLoader()
suite, load_failed = unittest.TestSuite(), []
for name in names:
    before = len(loader.errors)
    try:
        suite.addTest(loader.loadTestsFromName(name))
    except Exception:
        traceback.print_exc()
        load_failed.append(name)
        continue
    if len(loader.errors) > before:
        load_failed.append(name)
result = unittest.TextTestRunner(resultclass=Result, warnings=None if sys.warnoptions else "default").run(suite)
# 読み込めなかった名前の代わりの試験（_FailedTest）は、 load_failed に数えたので外す
failed = [t.id() for t, _ in result.failures + result.errors if not isinstance(t, unittest.loader._FailedTest)]
with open(out, "w", encoding="utf-8") as f:
    json.dump({"load_failed": load_failed, "in_import": result.in_import, "failed": failed}, f)
code = 0 if result.wasSuccessful() and not load_failed else 1
if code == 0 and result.testsRun == 0 and not result.skipped and sys.version_info >= (3, 12):
    code = 5                     # `python -m unittest` と同じ（試験が 1 つも流れなかった）
sys.exit(code)
'''


def _read_result(path: Path) -> "dict | None":
    """流し手が書いた試験の名前ごとの結果。 書けずに止まったときは None。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_failed(result: "dict | None") -> bool:
    """試験を読み込めずに落ちたか。 試験の名前ごとの結果で見る。

    読み込めなかった名前か、 試験が始まった後の読み込みの誤りで落ちた試験があり、 ほかに落ちた試験が
    無いとき。 ほかに落ちた試験があれば、 その試験が守りを確かめて落ちたので、 読み込みの失敗と数えない。
    **流し手が結果を書けずに止まったとき（None）も、 守りを確かめたことにしない**（#206 の 4-a。 前の PDM は
    「落ちた」と数えていた）。
    """
    if result is None:
        return True
    broken = list(result.get("load_failed", [])) + list(result.get("in_import", []))
    others = [t for t in result.get("failed", []) if t not in result.get("in_import", [])]
    return bool(broken) and not others


# ---------------------------------------------------------------- 当てると読めなくなる変異
def syntax_errors(table: Table, root: Path) -> "list[str]":
    """表の変異を 1 つずつ（ファイルに書かずに）当て、 当てた後に読めないものを返す。

    **当てると読めなくなる変異は、 守りを確かめたことにならない。** 対の試験は読み込みの失敗で落ち、
    「落ちた」と数えられる（両 Core の表に実際にあった）。 Python は compile（先頭の BOM は Python と同じく
    読み飛ばす）、 シェルは `bash -n`、 JSON は読めるか、 YAML は読めるか（PyYAML があれば）、 画面の script と
    `.js` は括弧の対応（`Brackets`）、 systemd の単位（`.service`）は各行が節か「名前=値」か。 元から読めない
    ものは見ない（当てて読めなくなったものだけを見る）。 置換前が見つからない行は、 ここでは見ない（本体の
    確かめが止める）。 見なかった種類は `syntax_notes()` が言う。
    """
    b = table.brackets
    js = _bracket_module(root, b)
    yaml = _yaml()
    out: "list[str]" = []
    texts: "dict[str, str]" = {}
    was_bad: "dict[str, object]" = {}          # 元のファイルの括弧の確かめ（ファイルごとに 1 回）
    for label, rel, before, after, check in table.rows:
        if rel not in texts:
            try:
                texts[rel] = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                texts[rel] = ""
        text = texts[rel]
        if not before or before not in text:
            continue
        new = text.replace(before, after, 1)
        why = None
        if rel.endswith(".py"):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")      # 文法の誤りだけを見る
                    # 先頭の BOM は、 Python がファイルを読むときと同じく読み飛ばす
                    compile(new[1:] if new.startswith("\ufeff") else new, rel, "exec")
            except SyntaxError as e:
                why = f"{rel} の {e.lineno} 行: {e.msg}"
        elif rel.endswith(".sh"):
            r = subprocess.run(["bash", "-n"], input=new, capture_output=True, text=True)
            if r.returncode != 0:
                why = f"{rel}: bash -n が通らない"
        elif rel.endswith(".json"):
            try:
                json.loads(new)
            except ValueError as e:
                why = f"{rel}: JSON として読めない（{e}）"
        elif rel.endswith((".yml", ".yaml")) and yaml is not None:
            try:
                yaml.safe_load(new)
            except yaml.YAMLError as e:
                why = f"{rel}: YAML として読めない（{str(e).splitlines()[0]}）"
        elif rel.endswith((".js", ".html")) and js is not None and not _checks_brackets(b.tests, check):
            find = (lambda s: b.js(js, s)) if rel.endswith(".js") else (lambda s: _html_script_bad(b, js, s))
            bad = find(new)
            if bad and not was_bad.setdefault(rel, find(text)):
                why = f"{rel}: {bad}"
        elif rel.endswith(".service"):
            line = _unit_bad_line(new)
            if line and not _unit_bad_line(text):
                why = f"{rel} の {line} 行: 節（[...]）でも「名前=値」でもない"
        if why:
            out.append(f"{label}: 当てると読めない（{why}）")
    return out


#: 文法を見る種類（`syntax_errors()`）。 ほかの種類は決まった文法が無いので見ない
SYNTAX_KINDS = (".py", ".sh", ".json", ".yml", ".yaml", ".js", ".html", ".service")


def _checks_brackets(tests: tuple, check: str) -> bool:
    """行の試験が**すべて**括弧の確かめの試験（クラスかその中の試験）か（#206 の 5-a）。

    1 つでもほかの試験（括弧の確かめのクラスを含むモジュール・パッケージの名前も、 ほかの試験を流すので
    ほかの試験に数える）があれば、 括弧を見る。"""
    names = check.split()
    return bool(names) and all(any(c == n or c.startswith(n + ".") for n in tests) for c in names)


def _bracket_module(root: Path, b: "Brackets | None"):
    """括弧の確かめを持つ試験のモジュール（写しの中のもの）。 無ければ None。"""
    if b is None:
        return None
    path = root / b.where
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_brackets_for_mutation", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _yaml():
    """PyYAML。 入っていなければ None（そのときは `.yml` を見ない）。"""
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def _html_script_bad(b: Brackets, js, html: str) -> "str | None":
    """画面の中の script（`src=` の無いもの）で、 括弧が対応していない最初のもの。"""
    for n, m in enumerate(b.scripts(js).finditer(html), 1):
        bad = b.js(js, m.group(1))
        if bad:
            return f"{n} 番目の script（{html[:m.start()].count(chr(10)) + 1} 行目から）: {bad}"
    return None


def _unit_bad_line(text: str) -> "int | None":
    """systemd の単位で、 空行・注釈・節（[...]）・「名前=値」（と \\ で続く行）のどれでもない最初の行。"""
    cont = False
    for i, ln in enumerate(text.splitlines(), 1):
        s = ln.strip()
        if cont:
            cont = s.endswith("\\")
            continue
        if not s or s[0] in "#;" or re.fullmatch(r"\[[^\]]+\]", s):
            continue
        if re.match(r"[A-Za-z][A-Za-z0-9]*\s*=", s):
            cont = s.endswith("\\")
            continue
        return i
    return None


def syntax_notes(table: Table, root: Path) -> "list[str]":
    """文法の確かめで、 見なかった・一部だけ見た種類を言う（黙って見ないことにしない）。"""
    rels = [rel for _label, rel, *_rest in table.rows]
    b = table.brackets
    notes = []
    web = [r for r in rels if r.endswith((".js", ".html"))]
    if web:
        if b is None:
            notes.append(f"画面の script と .js（{len(web)} 行）は見ない（括弧の確かめを持たない）")
        elif _bracket_module(root, b) is None:
            notes.append(f"画面の script と .js（{len(web)} 行）は見ない（括弧の確かめ {b.where} が見つからない）")
        else:
            notes.append(f"画面の script と .js（{len(web)} 行）は括弧の対応だけを見る"
                         "（JS を動かすものが無いので、 構文全部は見ない）")
            kept = sum(1 for _l, r, _b, _a, c in table.rows
                       if r.endswith((".js", ".html")) and _checks_brackets(b.tests, c))
            if kept:
                notes.append(f"括弧の対応そのものを確かめる試験（{', '.join(b.tests)}）だけと組んだ {kept} 行は、"
                             "わざと括弧を崩すので括弧を見ない")
    yml = [r for r in rels if r.endswith((".yml", ".yaml"))]
    if yml and _yaml() is None:
        notes.append(f".yml（{len(yml)} 行）は見ない（PyYAML が入っていない）")
    other = [r for r in rels if not r.endswith(SYNTAX_KINDS)]
    if other:
        names = ", ".join(sorted({Path(r).name for r in other}))
        notes.append(f"決まった文法が無いので見ない: {names}（{len(other)} 行）")
    return notes


# ---------------------------------------------------------------- 写し
def copy_repo(table: Table, dst: Path) -> None:
    """壊すための写しを作る。 **git が管理しているファイルと、 まだ追加していない新しいファイル（無視の設定に
    当たらないもの）だけを写し、 写しも git にする。**

    前は名前で除いていた。 名前は場所を問わず当たるので、 管理している `data/README.md` や `scripts/demo/` の
    道具まで抜け、 `.git` も無いので git を使う検査が写しの中で壊す前から落ちていた（その検査を使う変異は、
    守りを見ているかに関係なく「落ちた」と数えられていた）。 git の一覧なら CI の取り出しと同じ中身になり、
    手元にある版の管理の外の大きなもの（データ・学習の結果・モデル・.venv）も写さない。 git で読めないときは、
    Core が決めた除き方（`Table.fallback_ignore`）で写す。
    """
    root = table.root
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        shutil.copytree(root, dst, ignore=shutil.ignore_patterns(*table.fallback_ignore))
        return
    for rel in out.decode("utf-8").split("\0"):
        f = root / rel
        if not rel or not f.is_file():
            continue                             # 消したが git にまだ残っているもの
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst / rel)
    # `git ls-files` を使う検査があるので、 写しも git として読めるようにする
    # （元の .git は worktree だとファイルなので、 そのままでは写せない）
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"]):
        subprocess.run(cmd, cwd=str(dst), check=False, capture_output=True)


# ---------------------------------------------------------------- 分けて流す
def parse_part(argv: "list[str]") -> "tuple[int, int]":
    """`--part K/N`（表を N に分けた K 番目だけを流す）。 渡さなければ全部（1/1）。

    変異の job が時間の上限（15 分）を超えるようになったので、 上限を上げずに分けて回す。 どの行が K 番目に
    入るかは `plan_parts`。 分からない引数は、 何も流さずに断る（終了コード 2）。
    """
    if not argv:
        return 1, 1
    spec = None
    if len(argv) == 2 and argv[0] == "--part":
        spec = argv[1]
    elif len(argv) == 1 and argv[0].startswith("--part="):
        spec = argv[0][len("--part="):]
    m = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec or "")
    if not m or int(m[1]) > int(m[2]):
        print("使い方: python scripts/ci/mutation_check.py [--part K/N]（表を N に分けた K 番目。 1 ≦ K ≦ N）",
              file=sys.stderr)
        raise SystemExit(2)
    return int(m[1]), int(m[2])


#: 検査 1 つを流すのにかかる秒の下限（0 秒と数えると、 その検査の行を 1 つの部分へ寄せてしまう）
MIN_SECONDS = 0.1

#: 秒の表で使う値の上限（変異の job の上限は 15 分。 これを超える値は形の違う値として外す。 極端に大きい値で
#: 見積もりの足し算が溢れ、 分け方が例外で止まらないように）
MAX_SECONDS = 3600.0

#: `main` が出す、 行ごとの結果の行。 秒の表を作る道具（`seconds_main`）が読む。 説明に ` → ` が入ることが
#: あるので、 説明の側を長く取る（検査の名前には入らない）。 前の PDM の記録の「検査を読み込めずに」も読む
ROW_LINE = re.compile(r"^  (?:OK |NG ) (?P<label>.+) → (?P<test>.+?) "
                      r"は(?:落ちた|\*\*通ってしまった\*\*|\*\*(?:検査|試験)を読み込めずに落ちた\*\*)"
                      r"（(?P<secs>[0-9]+(?:\.[0-9]+)?) 秒）$")


def load_seconds(path: Path) -> "dict[str, float]":
    """秒の表（検査 → 行 1 つの秒）。 読めなければ空。 形の違う値（数でない・負・上限を超える）は外す。

    読めない・形の違う値は使わない（表に無い検査と同じく、 ほかの検査の中央値で数える。 部分の時間が
    そろわなくなるだけで、 どの行も 1 度ずつ流す）。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: "dict[str, float]" = {}
    for k, v in data.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        try:
            x = float(v)
        except OverflowError:                   # float にできないほど大きい整数
            continue
        if 0 <= x <= MAX_SECONDS:               # NaN と無限もここで外れる
            out[k] = x
    return out


def plan_parts(parts: int, rows, secs: "dict[str, float]") -> "list[list[int]]":
    """表の行の番号を parts に分ける。 どの行もちょうど 1 つの部分に入り、 部分の中は表の並びのまま。

    ・同じ検査の行は、 なるべく同じ部分に置く。 壊す前の確かめ（`baseline`）は、 部分の行が使う検査を
      1 度ずつ流すので、 同じ検査の行が部分に散ると、 その検査を部分の数だけ流す（番号の余りで分けていた
      ときは、 確かめだけで 1 つの部分に 1〜3 分かかっていた）
    ・部分ごとの見積もり（行の秒 + 確かめの秒）がそろうように、 重いまとまりから順に、 いちばん軽い部分へ
      置く（同じ重さなら表の前のもの・番号の小さい部分から）。 1 つの部分の目安を超えるまとまりは、 目安に
      収まる数の片に分ける（片ごとに確かめが要る）
    ・secs は検査ごとの秒。 表に無い検査は、 secs の値の中央値で数える
    """
    if parts == 1:
        return [list(range(len(rows)))]
    usual = statistics.median(secs.values()) if secs else 1.0
    groups: "dict[str, list[int]]" = {}
    for i, (*_, test) in enumerate(rows):
        groups.setdefault(test, []).append(i)
    cost = {t: max(secs.get(t, usual), MIN_SECONDS) for t in groups}
    target = sum((len(ix) + 1) * cost[t] for t, ix in groups.items()) / parts
    pieces: "list[tuple[float, list[int]]]" = []
    for t, ix in groups.items():
        c, n = cost[t], len(ix)
        k = n if c >= target else max(1, min(n, math.ceil(n * c / (target - c))))
        for j in range(k):
            piece = ix[j * n // k:(j + 1) * n // k]
            pieces.append(((len(piece) + 1) * c, piece))
    pieces.sort(key=lambda p: (-p[0], p[1][0]))
    load = [0.0] * parts
    out: "list[list[int]]" = [[] for _ in range(parts)]
    for c, piece in pieces:
        k = min(range(parts), key=lambda j: (load[j], j))
        load[k] += c
        out[k].extend(piece)
    return [sorted(p) for p in out]


def plan_of(table: Table, parts: int) -> "list[list[int]]":
    """表を parts に分けた行の番号（秒は Core の秒の表）。"""
    return plan_parts(parts, table.rows, load_seconds(table.seconds))


def rows_of(table: Table, part: int, parts: int) -> list:
    """表を parts に分けた part 番目の行（表の並びのまま）。 分け方は `plan_parts`。"""
    return [table.rows[i] for i in plan_of(table, parts)[part - 1]]


# ---------------------------------------------------------------- 壊す前の確かめ
def baseline(src: Path, rows) -> "list[str]":
    """壊す前の写しで、 行が使う検査がすべて通るかを確かめる。 通らないものを返す。

    **変異の確かめは「落ちた」を、 守りが効いた印と数える。** 壊す前から落ちる検査（依存の入れ忘れで読み込め
    ない・写しに無いファイルを読む など）があると、 その検査を使う変異がすべて「守りが効いた」に数えられ、
    何も確かめないまま緑になる。 CI では別の job で回すので、 ほかの段で入れたものに頼れない。

    まとめて 1 回回す（1 つずつ回すと CI で 2 分ほど延びる）。 落ちたときだけ 1 つずつ回して、 どれが落ちたかを示す。
    """
    checks = sorted({check for *_, check in rows})
    units = [c for c in checks if not c.startswith("script:")]
    scripts = [c for c in checks if c.startswith("script:")]
    bad: "list[str]" = []
    if units:
        ok, _ = run(src, " ".join(units))
        if not ok:
            for c in units:
                ok1, tail = run(src, c)
                if not ok1:
                    bad.append(f"{c}: 壊す前から落ちる\n" + tail)
            if not bad:
                bad.append("まとめて回すと落ちる（1 つずつなら通る。 テスト同士の混ざり方を見てください）")
    for c in scripts:
        ok, tail = run(src, c)
        if not ok:
            bad.append(f"{c}: 壊す前から落ちる\n" + tail)
    return bad


#: 時間のかかった行を、 遅い順に何件まで出すか
SLOWEST = 10


def report_seconds(timed: "list[tuple[float, str, str]]", base_secs: float) -> None:
    """行ごとにかかった秒の合計と、 遅い順の上位を出す。

    重い行や、 特定の検査のやり方に寄った行を見つけ、 照らす範囲を狭める・移すときの効き目を測るため。
    出すだけで、 合否は変えない。 行の頭は、 正本の検査の道具が試験の件数として数える形（`Ran N tests`・
    `OK (`・`FAILED (`）にしない。
    """
    if not timed:
        return
    total = sum(secs for secs, _, _ in timed)
    print(f"\n行ごとの時間: {len(timed)} 件で計 {total:.1f} 秒（壊す前の確かめ {base_secs:.1f} 秒）")
    print(f"時間のかかった行（遅い順に上位 {min(SLOWEST, len(timed))} 件）:")
    for secs, label, check in sorted(timed, key=lambda t: t[0], reverse=True)[:SLOWEST]:
        print(f"  {secs:7.1f} 秒  {label} → {check}")


# ---------------------------------------------------------------- 入口
def main(argv: "list[str] | None", table: Table) -> int:
    part, parts = parse_part(argv or [])
    rows = rows_of(table, part, parts)
    failures: "list[str]" = []
    timed: "list[tuple[float, str, str]]" = []
    base_secs = 0.0
    with tempfile.TemporaryDirectory(prefix="mirume_mut_") as td:
        src = Path(td) / "repo"
        copy_repo(table, src)
        # 当てると読めなくなる変異は、 試験が守りを確かめたことにならない（読み込みの失敗で落ちて「落ちた」と
        # 数える）。 壊す前に、 表の誤りとして止める（表の全部を見る。 どの部分でも同じに止まる）
        broken = syntax_errors(table, src)
        if broken:
            print(f"NG: 当てると読めなくなる変異があります（{len(broken)} 件）。 表の置き換える先を直してください")
            for b in broken:
                print("  " + b)
            return 1
        for note in syntax_notes(table, src):
            print(f"文法の確かめ: {note}")

        # 壊す前に、 行が使う検査が通ること。 通らなければ壊さずに止める
        t0 = time.monotonic()
        bad = baseline(src, rows)
        base_secs = time.monotonic() - t0
        if bad:
            print(f"NG: 壊す前から落ちる検査があります（{len(bad)} 件）。 依存の入れ忘れか、 テストの不具合です")
            for b in bad:
                print("  " + b.replace("\n", "\n      | "))
            return 1
        if parts > 1:
            print(f"表を {parts} に分けた {part} 番目の {len(rows)} 件（全部で {len(table.rows)} 件）を流します")
            missing = sorted({c for *_, c in rows} - set(load_seconds(table.seconds)))
            if missing:
                # 分け方だけに効く（どの行も流す）。 増えたら秒の表を作り直す
                print(f"秒の表に無い検査: {len(missing)} 個（ほかの検査の中央値で数えて分けた。 "
                      "作り直しは python scripts/ci/mutation_engine.py seconds <記録>...）")
        print(f"壊す前の確かめ: 表が使う {len({c for *_, c in rows})} 個の検査はすべて通った")

        for label, rel, before, after, check in rows:
            target = src / rel
            if not target.is_file():
                failures.append(f"{label}: {rel} が無い")
                continue
            original = target.read_text(encoding="utf-8")
            if before not in original:
                failures.append(f"{label}: 壊す対象が見つからない（{rel} の書き方が変わった？）")
                continue
            target.write_text(original.replace(before, after, 1), encoding="utf-8")
            t0 = time.monotonic()
            try:
                passed, tail = run(src, check)
            finally:
                target.write_text(original, encoding="utf-8")
            secs = time.monotonic() - t0
            timed.append((secs, label, check))
            # 試験を読み込めずに落ちたもの（流し手が結果を書けずに止まったものも）は、 守りを確かめたことにならない
            loaded = not tail.startswith(LOAD_FAILED)
            mark = "OK " if (not passed and loaded) else "NG "
            said = "**通ってしまった**" if passed else "落ちた" if loaded else "**試験を読み込めずに落ちた**"
            print(f"  {mark} {label} → {check} は{said}（{secs:.1f} 秒）")
            if not passed and not loaded:
                failures.append(f"{label}: {check} は試験を読み込めずに落ちた（試験が始まる前か、 流し手が結果を"
                                "書けずに止まったので、 この守りを検証していない。 置き換える先を、 読み込める形に"
                                "してください）")
                for ln in tail.splitlines():
                    print(f"        | {ln}")
            if passed:
                failures.append(f"{label}: {check} が落ちなかった（この守りを検証していない）")
                # **何が起きたのかを出す。** 手元で再現しない（CI でだけ通る）ときに、 これが無いと追いようがない
                for ln in tail.splitlines():
                    print(f"        | {ln}")

    report_seconds(timed, base_secs)
    if failures:
        print(f"\nNG: {len(failures)} 件")
        for f in failures:
            print(f"  {f}")
        return 1
    if parts > 1:
        # 分けて流したときは、 その部分だけと分かる言い方にする（ほかの部分は別の job が流す）
        print(f"\nOK: 表を {parts} に分けた {part} 番目（{len(rows)} 件）の守りについて、 壊すとテストが落ちます"
              f"（ほかの部分は別に流す）")
    else:
        print("\nOK: すべての守りについて、 壊すとテストが落ちます")
    return 0


# ---------------------------------------------------------------- 秒の表を作る道具
def seconds_of(texts: "list[str]") -> "tuple[dict[str, float], int]":
    """記録の文から、 検査ごとの行 1 つの平均の秒と、 読んだ結果の行の数。"""
    got: "dict[str, list[float]]" = {}
    for text in texts:
        for ln in text.splitlines():
            m = ROW_LINE.match(ln)
            if m:
                got.setdefault(m["test"], []).append(float(m["secs"]))
    table = {t: round(sum(v) / len(v), 2) for t, v in got.items()}
    return table, sum(len(v) for v in got.values())


def seconds_main(argv: "list[str]", out: "Path | None" = None) -> int:
    """秒の表を作り直す。 `[--out <道>] <変異の job の記録>...`（既定の書き先は、 この file と並ぶ `SECONDS_NAME`）。

    記録は、 正本の検査の道具（`run_required_verification.py`）が終わりに出す「段ごとの記録」の場所の
    `*mutation*/` の下のファイル（`mutation_check.py` の出力でもよい）。 行ごとの結果の行
    （`  OK  <説明> → <検査> は落ちた（<秒> 秒）`）を読み、 検査ごとに行 1 つの平均の秒を書く。 表のすべての部分の
    記録を渡す（渡さなかった部分の検査は表から落ち、 ほかの検査の中央値で数える）。 結果の行が 1 つも無いときは、
    書かずに断る（終了コード 2）。
    """
    args = list(argv)
    out = out or HERE / SECONDS_NAME
    if args[:1] == ["--out"]:
        if len(args) < 2:
            args = []
        else:
            out, args = Path(args[1]), args[2:]
    if not args or any(a.startswith("-") for a in args):
        print("使い方: python scripts/ci/mutation_engine.py seconds [--out <道>] <変異の job の記録>...",
              file=sys.stderr)
        return 2
    texts = [Path(a).read_text(encoding="utf-8", errors="replace") for a in args]
    table, n = seconds_of(texts)
    if not table:
        print("記録に、 行ごとの結果の行がありません（書きませんでした）", file=sys.stderr)
        return 2
    out.write_text(json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"検査 {len(table)} 個（結果の行 {n} 件）の秒を書きました: {out.name}")
    return 0


# ---------------------------------------------------------------- workflow の照らし（各 Core の試験が使う）
def workflow_parts(wf: str) -> "list[tuple[int, int]]":
    """workflow の中で変異の道具（`mutation_check.py`）を流す行の (K, N)。 `--part K/N` か `--part=K/N` だけを受ける。

    分けない・ほかの引数が付く・数でない書き方は、 何を流すか確かめられないので ValueError（すり抜けさせない）。
    `python3` や前の書き方は問わない。 注釈の行は見ない。
    """
    out = []
    for ln in wf.splitlines():
        s = ln.strip()
        if "mutation_check.py" not in s or s.startswith("#"):
            continue
        args = shlex.split(s.split("mutation_check.py", 1)[1])
        spec = (args[1] if len(args) == 2 and args[0] == "--part"
                else args[0][len("--part="):] if len(args) == 1 and args[0].startswith("--part=") else None)
        m = re.fullmatch(r"([0-9]+)/([0-9]+)", spec or "")
        if m is None:
            raise ValueError(f"変異の道具の流し方が分からない（--part K/N で 1 つの部分を流す）: {s}")
        out.append((int(m[1]), int(m[2])))
    return out


def workflow_jobs(wf: str) -> "dict[str, str]":
    """workflow の job の名前と、 その中身の文（2 文字下げの名前から次の job まで）。"""
    body = wf.split("\njobs:\n", 1)[1] if "\njobs:\n" in wf else ""
    out, name = {}, None
    for ln in body.splitlines():
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", ln)
        if m:
            name = m[1]
            out[name] = ""
        elif name is not None:
            out[name] += ln + "\n"
    return out


def workflow_problems(wf: str) -> "list[str]":
    """workflow の変異の job の形の問題。 無ければ空。

    ・変異の道具を流し、 分け方（N）がそろい、 部分（K）が 1〜N まで漏れなく並ぶ
    ・分けた job は、 部分の番号のほかは同じ中身（if・continue-on-error・timeout-minutes・段。 片方だけ緩めると、
      その部分の行の確かめ方が変わる）。 job の中の注釈と空行は見ない。 if と timeout-minutes がある
    """
    try:
        calls = workflow_parts(wf)
    except ValueError as e:
        return [str(e)]
    if not calls:
        return ["workflow が変異の道具を流していない"]
    out = []
    ns = {n for _k, n in calls}
    if len(ns) != 1:
        out.append(f"分け方（N）がそろっていない: {sorted(ns)}")
    elif sorted(k for k, _n in calls) != list(range(1, calls[0][1] + 1)):
        out.append("部分（K）が 1〜N まで漏れなく並んでいない")
    jobs = {k: v for k, v in workflow_jobs(wf).items() if "mutation_check.py" in v}
    norm = {re.sub(r"[0-9]+/[0-9]+", "K/N", "\n".join(
        ln for ln in v.splitlines() if ln.strip() and not ln.lstrip().startswith("#"))) for v in jobs.values()}
    if len(norm) > 1:
        out.append("分けた変異の job の中身が、 部分の番号のほかで違う")
    for key in ("if:", "timeout-minutes:"):
        if any(key not in v for v in norm):
            out.append(f"変異の job に {key} が無い")
    return out


# ---------------------------------------------------------------- 仕組み自身を壊す行
#: 仕組み自身を壊す行（壊す先はこの file、 照らすのは共通の試験 `scripts/ci/test_mutation_engine.py`）。
#: 各 Core が表（`Table.rows`）に足して流す（正本には変異の job が無いので、 正本では流さない。 共通の試験が、
#: どの行も壊す先を見つけ、 当てても読め、 照らす試験があることを確かめる）。 前は各 Core が同じ行を自分の写しに
#: 向けて持っていた（PDM 44 行・撮像側 57 行）。
_T = ENGINE_TESTS
ENGINE_MUTATIONS = (
    # ---- 壊す前の確かめと、 行ごとの秒
    ("変異の仕組み: 壊す前の確かめをしない（入れ忘れた依存で落ちる検査を、 守りが効いたと数える）",
     ENGINE_FILE, "        bad = baseline(src, rows)\n", "        bad = []\n",
     f"{_T}.BaselineTest.test_a_check_that_fails_before_breaking_stops_the_run"),
    ("変異の仕組み: まとめて回して落ちたときに、 1 つずつ回して落ちた検査を言わない",
     ENGINE_FILE, '                    bad.append(f"{c}: 壊す前から落ちる\\n" + tail)\n            if not bad:\n',
     "                    pass\n            if not bad:\n",
     f"{_T}.BaselineTest.test_a_check_that_fails_before_breaking_stops_the_run"),
    ("変異の仕組み: script: の検査の、 壊す前の確かめをしない",
     ENGINE_FILE, "    for c in scripts:\n        ok, tail = run(src, c)\n",
     "    for c in ():\n        ok, tail = run(src, c)\n",
     f"{_T}.BaselineTest.test_a_script_check_that_fails_before_breaking_stops_the_run"),
    ("変異の仕組み: 行ごとにかかった秒を出さない（重い行を見つけられない）",
     ENGINE_FILE, '            print(f"  {mark} {label} → {check} は{said}（{secs:.1f} 秒）")\n',
     '            print(f"  {mark} {label} → {check} は{said}")\n',
     f"{_T}.RowSecondsTest.test_each_row_says_its_seconds_and_the_total"),
    ("変異の仕組み: 時間のかかった行を遅い順に並べない（速い行が上に出る）",
     ENGINE_FILE, "    for secs, label, check in sorted(timed, key=lambda t: t[0], reverse=True)[:SLOWEST]:\n",
     "    for secs, label, check in sorted(timed, key=lambda t: t[0])[:SLOWEST]:\n",
     f"{_T}.RowSecondsTest.test_the_slowest_rows_come_first_up_to_the_limit"),
    ("変異の仕組み: 行ごとの時間の合計と上位を出さない",
     ENGINE_FILE, "    report_seconds(timed, base_secs)\n", "",
     f"{_T}.RowSecondsTest.test_each_row_says_its_seconds_and_the_total"),
    # ---- 行を流す
    ("変異の仕組み: 通ってしまった行を失敗に数えない",
     ENGINE_FILE, '                failures.append(f"{label}: {check} が落ちなかった（この守りを検証していない）")\n',
     "                pass\n",
     f"{_T}.BaselineTest.test_a_row_that_passes_fails_and_says_what_happened"),
    ("変異の仕組み: 通ってしまった行で、 何が起きたのか（出力の末尾）を出さない",
     ENGINE_FILE, "                # **何が起きたのかを出す。** 手元で再現しない（CI でだけ通る）ときに、 これが無いと追いようがない\n"
                  "                for ln in tail.splitlines():\n",
     "                for ln in ():\n",
     f"{_T}.BaselineTest.test_a_row_that_passes_fails_and_says_what_happened"),
    ("変異の仕組み: 壊す対象が見つからない行を、 失敗に数えない",
     ENGINE_FILE, '                failures.append(f"{label}: 壊す対象が見つからない（{rel} の書き方が変わった？）")\n',
     "                pass\n",
     f"{_T}.BaselineTest.test_a_row_whose_target_is_gone_is_a_failure"),
    ("変異の仕組み: 壊す先のファイルが無い行を、 失敗に数えない",
     ENGINE_FILE, '                failures.append(f"{label}: {rel} が無い")\n', "                pass\n",
     f"{_T}.BaselineTest.test_a_row_whose_target_is_gone_is_a_failure"),
    ("変異の仕組み: 壊した写しを、 次の行の前に元へ戻さない",
     ENGINE_FILE, "            finally:\n                target.write_text(original, encoding=\"utf-8\")\n",
     "            finally:\n                pass\n",
     f"{_T}.BaselineTest.test_each_row_starts_from_the_original"),
    ("変異の仕組み: 検査にバイトコードを書かせる（前の変異の .pyc を使って、 変異が効かない）",
     ENGINE_FILE, '    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}\n', "    env = dict(os.environ)\n",
     f"{_T}.BaselineTest.test_no_bytecode_is_written"),
    ("変異の仕組み: 検査に、 流す人の環境の作業ルートとデータの起点をそのまま渡す",
     ENGINE_FILE,
     '    env.update({WORK_MARK: str(work), "MIRUME_WORK_ROOT": str(work), "MIRUME_DATA_ROOT": str(work / "data")})\n',
     "",
     f"{_T}.BaselineTest.test_each_run_gets_its_own_work_root"),
    ("変異の仕組み: 検査に渡した一時の作業ルートを、 流し終えても消さない（変異の記録が残る）",
     ENGINE_FILE, "    finally:\n        shutil.rmtree(scratch, ignore_errors=True)\n", "    finally:\n        pass\n",
     f"{_T}.BaselineTest.test_each_run_gets_its_own_work_root"),
    ("変異の仕組み: script: の .sh を bash でなく python で流す",
     ENGINE_FILE, '        cmd = ["bash", rel] if rel.endswith(".sh") else [sys.executable, rel]\n',
     "        cmd = [sys.executable, rel]\n",
     f"{_T}.BaselineTest.test_a_shell_script_check_runs_with_bash"),
    # ---- 写し
    ("変異の仕組み: 写しを git にしない（git を使う検査が、 写しの中で壊す前から落ちる）",
     ENGINE_FILE, '    for cmd in (["git", "init", "-q"], ["git", "add", "-A"]):\n', "    for cmd in ():\n",
     f"{_T}.CopyTest.test_the_copy_is_what_git_has"),
    ("変異の仕組み: 無視の設定に当たるファイルも写す",
     ENGINE_FILE, '"--others", "--exclude-standard"],\n', '"--others"],\n',
     f"{_T}.CopyTest.test_the_copy_is_what_git_has"),
    ("変異の仕組み: git で読めないときの予備の除き方に、 Core の決めたものを使わない",
     ENGINE_FILE, "ignore=shutil.ignore_patterns(*table.fallback_ignore))\n",
     'ignore=shutil.ignore_patterns(".git"))\n',
     f"{_T}.CopyTest.test_without_git_the_core_fallback_is_used"),
    # ---- 分けて流す
    ("変異の仕組み: 表を分けても、 どの部分も全部の行を流す（分けた意味が無い・同じ行を何度も流す）",
     ENGINE_FILE, "    return [table.rows[i] for i in plan_of(table, parts)[part - 1]]\n",
     "    return list(table.rows)\n",
     f"{_T}.PartTest.test_the_parts_run_every_row_once"),
    ("変異の仕組み: 部分を選んでも、 表の全部の行を流す",
     ENGINE_FILE, "        for label, rel, before, after, check in rows:\n",
     "        for label, rel, before, after, check in table.rows:\n",
     f"{_T}.BaselineTest.test_a_part_runs_only_its_rows"),
    ("変異の仕組み: 分け方の数より大きい部分（3/2）を断らない（どの行も流さずに通る）",
     ENGINE_FILE, "    if not m or int(m[1]) > int(m[2]):\n", "    if not m:\n",
     f"{_T}.PartTest.test_unknown_arguments_are_refused"),
    ("変異の仕組み: 分けて流したときも「すべての守り」と言う（その部分だけを確かめたと分からない）",
     ENGINE_FILE, "    if parts > 1:\n        # 分けて流したときは、 その部分だけと分かる言い方にする",
     "    if False:\n        # 分けて流したときは、 その部分だけと分かる言い方にする",
     f"{_T}.BaselineTest.test_a_part_runs_only_its_rows"),
    ("変異の分け方: 同じ検査の行をまとめない（壊す前の確かめで、 同じ検査を部分の数だけ流す）",
     ENGINE_FILE, "        groups.setdefault(test, []).append(i)\n", '        groups.setdefault(f"{test}#{i}", []).append(i)\n',
     f"{_T}.SpreadTest.test_rows_of_one_check_stay_together"),
    ("変異の分け方: 1 つの部分の目安を超えるまとまりを分けない",
     ENGINE_FILE, "        k = n if c >= target else max(1, min(n, math.ceil(n * c / (target - c))))\n",
     "        k = 1\n",
     f"{_T}.SpreadTest.test_a_heavy_check_is_split"),
    ("変異の分け方: いちばん重い部分へ置く（部分の時間がそろわない）",
     ENGINE_FILE, "        k = min(range(parts), key=lambda j: (load[j], j))\n",
     "        k = min(range(parts), key=lambda j: (-load[j], j))\n",
     f"{_T}.SpreadTest.test_the_parts_take_about_the_same_time"),
    ("変異の分け方: 重いまとまりから置かない（表の並びで置く）",
     ENGINE_FILE, "    pieces.sort(key=lambda p: (-p[0], p[1][0]))\n", "    pieces.sort(key=lambda p: p[1][0])\n",
     f"{_T}.SpreadTest.test_the_parts_take_about_the_same_time"),
    ("変異の分け方: 表に無い検査を 0 秒と数える（その行を軽い部分へ寄せる）",
     ENGINE_FILE, "    usual = statistics.median(secs.values()) if secs else 1.0\n", "    usual = 0.0\n",
     f"{_T}.SpreadTest.test_checks_missing_from_the_table_count_as_the_usual_seconds"),
    ("変異の分け方: 秒の表を読まない",
     ENGINE_FILE, "    return plan_parts(parts, table.rows, load_seconds(table.seconds))\n",
     "    return plan_parts(parts, table.rows, {})\n",
     f"{_T}.SpreadTest.test_the_table_is_used_when_no_seconds_are_given"),
    ("変異の分け方: 秒の表の負の値を外さない",
     ENGINE_FILE, "        if 0 <= x <= MAX_SECONDS:               # NaN と無限もここで外れる\n",
     "        if x <= MAX_SECONDS:\n",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: 秒の表の上限を超える値を使う（見積もりが溢れて、 分け方が例外で止まる）",
     ENGINE_FILE, "        if 0 <= x <= MAX_SECONDS:               # NaN と無限もここで外れる\n", "        if 0 <= x:\n",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: 秒の表の真偽の値を数として使う",
     ENGINE_FILE,
     "        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)):\n",
     "        if not isinstance(k, str) or not isinstance(v, (int, float)):\n",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: 秒の表が JSON の物でないときに断らない（壊れた表で止まる）",
     ENGINE_FILE, "    if not isinstance(data, dict):\n        return {}\n    out: ", "    out: ",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: float にできないほど大きい整数で止まる",
     ENGINE_FILE, "        except OverflowError:                   # float にできないほど大きい整数\n            continue\n",
     "        except ZeroDivisionError:\n            continue\n",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: 深い入れ子の秒の表で止まる",
     ENGINE_FILE, "    except (OSError, ValueError, RecursionError):\n        return {}\n    if not isinstance(data, dict):\n",
     "    except (OSError, ValueError):\n        return {}\n    if not isinstance(data, dict):\n",
     f"{_T}.SpreadTest.test_a_broken_table_still_runs_every_row_once"),
    ("変異の分け方: 部分の中の行を表の並びにしない",
     ENGINE_FILE, "    return [sorted(p) for p in out]\n", "    return out\n",
     f"{_T}.SpreadTest.test_the_same_plan_every_time_and_rows_keep_the_table_order"),
    ("変異の分け方: 秒の表に無い検査の数を言わない（表を作り直す目安が無い）",
     ENGINE_FILE, "            if missing:\n", "            if False:\n",
     f"{_T}.BaselineTest.test_a_part_runs_only_its_rows"),
    ("変異の分け方: まとまりを切った片が隣の片と重なる（同じ行を 2 度流す）",
     ENGINE_FILE, "            piece = ix[j * n // k:(j + 1) * n // k]\n", "            piece = ix[j * n // k:(j + 1) * n // k + 1]\n",
     f"{_T}.SpreadTest.test_a_heavy_check_is_split"),
    ("変異の分け方: まとまりを切った片の間で行が抜ける（その行を流さない）",
     ENGINE_FILE, "            piece = ix[j * n // k:(j + 1) * n // k]\n", "            piece = ix[j * n // k + 1:(j + 1) * n // k]\n",
     f"{_T}.SpreadTest.test_a_heavy_check_is_split"),
    ("変異の分け方: 0 秒の検査を下限で数えない（その行を 1 つの部分へ寄せる）",
     ENGINE_FILE, "    cost = {t: max(secs.get(t, usual), MIN_SECONDS) for t in groups}\n",
     "    cost = {t: secs.get(t, usual) for t in groups}\n",
     f"{_T}.SpreadTest.test_checks_of_zero_seconds_are_still_spread"),
    # ---- 当てると読めなくなる変異
    ("変異の仕組み: 当てると読めなくなる変異を止めない（読み込みの失敗で落ちて、 守りが効いたと数える）",
     ENGINE_FILE, "        broken = syntax_errors(table, src)\n", "        broken = []\n",
     f"{_T}.SyntaxTest.test_a_mutation_that_breaks_the_syntax_stops_the_run"),
    ("変異の仕組み: 当てた後の Python の文法を見ない",
     ENGINE_FILE, '                    compile(new[1:] if new.startswith("\\ufeff") else new, rel, "exec")\n',
     "                    pass\n",
     f"{_T}.SyntaxTest.test_a_mutation_that_breaks_the_syntax_stops_the_run {_T}.SyntaxTest.test_an_emptied_block_is_caught"),
    ("変異の仕組み: 先頭に BOM のある .py を読めないと止める",
     ENGINE_FILE, '                    compile(new[1:] if new.startswith("\\ufeff") else new, rel, "exec")\n',
     '                    compile(new, rel, "exec")\n',
     f"{_T}.SyntaxTest.test_a_py_with_a_bom_reads"),
    ("変異の仕組み: 当てた後のシェルの文法を見ない",
     ENGINE_FILE, '            if r.returncode != 0:\n                why = f"{rel}: bash -n が通らない"\n',
     '            if False:\n                why = f"{rel}: bash -n が通らない"\n',
     f"{_T}.SyntaxTest.test_shell_and_json_are_read_too"),
    ("変異の仕組み: 当てた後の JSON が読めるかを見ない",
     ENGINE_FILE, "                json.loads(new)\n", "                pass\n",
     f"{_T}.SyntaxTest.test_shell_and_json_are_read_too"),
    ("変異の仕組み: 当てた後の YAML が読めるかを見ない",
     ENGINE_FILE, "                yaml.safe_load(new)\n", "                pass\n",
     f"{_T}.SyntaxTest.test_yaml_is_read_too"),
    ("変異の仕組み: 当てた後の画面の script と .js の括弧を見ない",
     ENGINE_FILE, "            bad = find(new)\n", "            bad = None\n",
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 元から括弧が崩れているファイルも止める（当てて読めなくなったものだけを見ない）",
     ENGINE_FILE, "            if bad and not was_bad.setdefault(rel, find(text)):\n", "            if bad:\n",
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 括弧の対応そのものを確かめる試験と組んだ変異（わざと崩す）も止める",
     ENGINE_FILE, " and js is not None and not _checks_brackets(b.tests, check):\n", " and js is not None:\n",
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 括弧の確かめを、 行の試験が 1 つでも括弧の確かめの試験なら外す（#206 の 5-b に戻す）",
     ENGINE_FILE,
     '    return bool(names) and all(any(c == n or c.startswith(n + ".") for n in tests) for c in names)\n',
     '    return bool(names) and any(any(c == n or c.startswith(n + ".") for n in tests) for c in names)\n',
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 括弧の確かめから、 括弧の確かめのクラスを含むモジュールの名前と組んだ行も外す",
     ENGINE_FILE,
     '    return bool(names) and all(any(c == n or c.startswith(n + ".") for n in tests) for c in names)\n',
     '    return bool(names) and all(any(c == n or c.startswith(n + ".") or n.startswith(c + ".")'
     " for n in tests) for c in names)\n",
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 括弧の確かめから外した行の数を言わない",
     ENGINE_FILE, "            if kept:\n", "            if False:\n",
     f"{_T}.SyntaxTest.test_page_scripts_and_js_are_read_for_brackets"),
    ("変異の仕組み: 当てた後の systemd の単位を見ない",
     ENGINE_FILE, "            line = _unit_bad_line(new)\n", "            line = None\n",
     f"{_T}.SyntaxTest.test_systemd_units_are_read"),
    ("変異の仕組み: 文法を見なかった種類を言わない（黙って見ないことにする）",
     ENGINE_FILE, '        for note in syntax_notes(table, src):\n            print(f"文法の確かめ: {note}")\n', "",
     f"{_T}.SyntaxTest.test_kinds_without_a_grammar_are_named"),
    ("変異の仕組み: 括弧の確かめが見つからないときに、 見ないと言わない",
     ENGINE_FILE, '        elif _bracket_module(root, b) is None:\n            notes.append(',
     '        elif False:\n            notes.append(',
     f"{_T}.SyntaxTest.test_without_the_bracket_checker_it_says_so"),
    # ---- 読み込めずに落ちた試験と、 結果を書けずに止まった流し手
    ("変異の仕組み: 試験を読み込めずに落ちたのを、 守りが効いたと数える",
     ENGINE_FILE, "            if not passed and not loaded:\n                failures.append(",
     "            if False:\n                failures.append(",
     f"{_T}.SyntaxTest.test_a_test_that_cannot_be_loaded_is_not_counted_as_caught"),
    ("変異の仕組み: 読み込みの失敗を見分けない",
     ENGINE_FILE, "    if r.returncode != 0 and result_file is not None and _load_failed(result):\n", "    if False:\n",
     f"{_T}.SyntaxTest.test_a_test_that_cannot_be_loaded_is_not_counted_as_caught"),
    ("変異の仕組み: 読み込むときの NameError など（試験を 1 つも流さずに止まる）を見分けない",
     ENGINE_FILE, "        traceback.print_exc()\n        load_failed.append(name)\n", "        traceback.print_exc()\n",
     f"{_T}.SyntaxTest.test_a_test_that_cannot_be_loaded_is_not_counted_as_caught"),
    ("変異の仕組み: 名前を並べた行で 1 つだけ読み込めないとき、 ほかの試験が捕まえたのを読み込みの失敗と数える",
     ENGINE_FILE, "    return bool(broken) and not others\n", "    return bool(broken)\n",
     f"{_T}.SyntaxTest.test_one_unloadable_name_beside_a_test_that_caught"),
    ("変異の仕組み: 試験が始まった後の読み込みの誤り（setUp で読み込むモジュールの NameError など）を見分けない",
     ENGINE_FILE, "            self.in_import.append(test.id())\n", "            pass\n",
     f"{_T}.SyntaxTest.test_an_import_error_after_the_test_started_is_not_counted_as_caught"),
    ("変異の仕組み: モジュールの本体の誤りなら、 読み込みの誤りでない例外も読み込みの失敗と数える",
     ENGINE_FILE, '        if issubclass(err[0], IMPORT_ERRORS) and "<module>" in frames:\n',
     '        if "<module>" in frames:\n',
     f"{_T}.SyntaxTest.test_an_import_error_after_the_test_started_is_not_counted_as_caught"),
    ("変異の仕組み: 試験が始まった後の読み込みの誤りを、 最も内側の枠が <module> のときだけ見分ける",
     ENGINE_FILE, '        if issubclass(err[0], IMPORT_ERRORS) and "<module>" in frames:\n',
     '        if issubclass(err[0], IMPORT_ERRORS) and frames[-1:] == ["<module>"]:\n',
     f"{_T}.SyntaxTest.test_an_import_error_after_the_test_started_is_not_counted_as_caught"
     f" {_T}.SyntaxTest.test_an_unloadable_name_beside_a_late_import_error_is_not_caught"),
    ("変異の仕組み: subTest の中の読み込みの誤りを見分けない",
     ENGINE_FILE, "        if err is not None:\n            self.note(subtest, err)\n",
     "        if err is not None:\n            pass\n",
     f"{_T}.SyntaxTest.test_an_import_error_in_a_subtest_is_not_counted_as_caught"),
    ("変異の仕組み: 読み込めなかった名前の代わりの試験（_FailedTest）を、 ほかに落ちた試験と数える",
     ENGINE_FILE,
     "failed = [t.id() for t, _ in result.failures + result.errors if not isinstance(t, unittest.loader._FailedTest)]\n",
     "failed = [t.id() for t, _ in result.failures + result.errors]\n",
     f"{_T}.SyntaxTest.test_a_test_that_cannot_be_loaded_is_not_counted_as_caught"),
    ("変異の仕組み: 読み込めなかった名前（ImportError の _FailedTest）を数えない",
     ENGINE_FILE, "    if len(loader.errors) > before:\n", "    if False:\n",
     f"{_T}.SyntaxTest.test_a_test_that_cannot_be_loaded_is_not_counted_as_caught"),
    ("変異の仕組み: 流し手が結果を書けずに止まったのを、 守りが効いたと数える（#206 の 4-b に戻す）",
     ENGINE_FILE, "    if result is None:\n        return True\n", "    if result is None:\n        return False\n",
     f"{_T}.SyntaxTest.test_the_load_check_reads_results_per_test"
     f" {_T}.SyntaxTest.test_a_runner_that_stops_without_a_result_is_not_counted_as_caught"),
    ("変異の仕組み: 流し手の結果が読めないときに、 空の結果として読む",
     ENGINE_FILE, "    except (OSError, ValueError):\n        return None\n    return data if",
     "    except (OSError, ValueError):\n        return {}\n    return data if",
     f"{_T}.SyntaxTest.test_the_load_check_reads_results_per_test"),
    ("変異の仕組み: 流し手が、 落ちた試験があっても通ったと返す",
     ENGINE_FILE, "code = 0 if result.wasSuccessful() and not load_failed else 1\n", "code = 0\n",
     f"{_T}.SyntaxTest.test_a_mutation_that_reads_is_still_used_to_break"),
    ("変異の仕組み: 流し手が作業の場所を絶対の道で読まない（試験の中で作業の場所を変えると読めない）",
     ENGINE_FILE, 'if sys.path and sys.path[0] == "":\n    sys.path[0] = os.getcwd()\n', "",
     f"{_T}.SyntaxTest.test_the_runner_finds_modules_after_a_chdir"),
    # ---- 秒の表を作る道具
    ("秒の表を作る道具: 説明に ` → ` がある行で、 検査の名前を取り違える",
     ENGINE_FILE, '(?P<label>.+) → (?P<test>.+?) "\n', '(?P<label>.+?) → (?P<test>.+?) "\n',
     f"{_T}.SecondsToolTest.test_the_tool_reads_the_lines_main_prints"),
    ("秒の表を作る道具: 前の PDM の記録（検査を読み込めずに）を読まない",
     ENGINE_FILE, r'\*\*(?:検査|試験)を読み込めずに落ちた\*\*', r'\*\*試験を読み込めずに落ちた\*\*',
     f"{_T}.SecondsToolTest.test_both_words_of_the_load_failure_are_read"),
    ("秒の表を作る道具: 行の平均でなく、 いちばん遅い行の秒を使う",
     ENGINE_FILE, "    table = {t: round(sum(v) / len(v), 2) for t, v in got.items()}\n",
     "    table = {t: round(max(v), 2) for t, v in got.items()}\n",
     f"{_T}.SecondsToolTest.test_each_check_gets_the_mean_seconds_of_its_rows"),
    ("秒の表を作る道具: 結果の行が無くても書く（秒の表が空になる）",
     ENGINE_FILE, "    if not table:\n", "    if False:\n",
     f"{_T}.SecondsToolTest.test_nothing_is_written_without_result_lines"),
    # ---- workflow の照らし
    ("workflow の照らし: 部分（K）の漏れを見ない",
     ENGINE_FILE, "    elif sorted(k for k, _n in calls) != list(range(1, calls[0][1] + 1)):\n", "    elif False:\n",
     f"{_T}.WorkflowTest.test_problems_are_found"),
    ("workflow の照らし: 分けた job の中身の違いを見ない",
     ENGINE_FILE, "    if len(norm) > 1:\n", "    if False:\n",
     f"{_T}.WorkflowTest.test_problems_are_found"),
    ("workflow の照らし: ほかの引数が付く流し方を通す",
     ENGINE_FILE, "        if m is None:\n            raise ValueError(", "        if False:\n            raise ValueError(",
     f"{_T}.WorkflowTest.test_unusual_ways_to_run_the_tool_are_not_let_through"),
)
del _T


if __name__ == "__main__":
    if sys.argv[1:2] == ["seconds"]:
        sys.exit(seconds_main(sys.argv[2:]))
    print("使い方: python scripts/ci/mutation_engine.py seconds [--out <道>] <変異の job の記録>...\n"
          "（変異の確かめは、 各 Core の python scripts/ci/mutation_check.py [--part K/N] で流す）", file=sys.stderr)
    sys.exit(2)

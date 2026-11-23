#!/usr/bin/env python3
"""顧客の値の混入検査が働くことを確かめる。

**このリポジトリは Public。** それなのに混入検査が無かった。 Private な
Core 2 件にはあり、 **必要な順番が逆になっていた**。

Private 側では実際に 3 回混入した。 どれも説明文・テストの見本・コメントに
顧客名を書いたもので、 **書いた本人は混入だと思っていなかった**。

**この試験は、 検査と一緒に写しとして Core にも置く**（`copy_sync_check.py` の
`SHARED`。 #206）。 前は各 Core が手で写して直し、 確かめを足した所が
Repository ごとにずれていた。 そのため、 ここに書く確かめは、 どの Repository の
木の上でも同じ意味で流れる形にする（正本にしか無いファイルを読む確かめは、
そのファイルがあるときだけ見る）。 Repository に固有の確かめ（その Repository に
しか無い置き場所を見るもの）は、 その Repository の試験に置く。

**試験の値はすべて架空。** 検査や試験に実在の値を書くと、 検査とこの試験を
`.ci-leak-allow` で除外しないと通らなくなり、 除外がその値を検査から隠す。
そうならないように:

  ・見つけるべき値（私用の IP・シリアルらしい並び）は実行するときに組み立てる。
    このファイル自体は検査を通るので、 除外の一覧に頼らない

**見つけた値は出力しない**（種類・ファイル・行だけ）。 **通常の CI に secret を
渡さない**（PR の code が動くので、 書き込み権限で secret を読み出せてしまう）。
**追跡しているファイルは、 道にどんな文字があっても読む。** どれもここで確かめる。

**顧客の名前は照合しない**（Core は一般的な名前で実装する決まり。 前の登録表と
照らす形は `MirumeAI/.github#209` で外した）。 外した引数を渡すと、 合格にせずに
止まることだけを確かめる。

見本は通す。 `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に予約された
アドレスで、 繋ぎに行っても届かない。
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import customer_data_check as C                            # noqa: E402

#: 架空の値。 **このファイルに検査が拾う形で書かない**（実行するときに組み立てる）。
SITE_IP_192 = "192.168." + "0.10"
SITE_IP_10 = "10." + "0.0.10"
SITE_IP_172 = "172.16." + "0.10"
LINK_LOCAL = "169.254." + "0.10"
SERIAL = "TST" + "00000001"

#: 見つかるべき値。
FOUND = {
    "現場の IP（192.168）": f"host = {SITE_IP_192}\n",
    "現場の IP（10.x）": f"plc = {SITE_IP_10}\n",
    "現場の IP（172.16-31）": f"nas = {SITE_IP_172}\n",
    "リンクローカル": f"cam = {LINK_LOCAL}\n",
    "JSON の中の IP": f'{{"host": "{SITE_IP_192}"}}\n',
    "シリアルらしい並び": f"serial = {SERIAL}\n",
}

#: 通すべき値。
PASSED = {
    "文書用の予約 IP（192.0.2）": "host = 192.0.2.20\n",
    "文書用の予約 IP（198.51.100）": "host = 198.51.100.7\n",
    "文書用の予約 IP（203.0.113）": "host = 203.0.113.9\n",
    "ループバック": "host = 127.0.0.1\n",
    "すべてのアドレス": "host = 0.0.0.0\n",
    "ネットマスク": "mask = 255.255.255.0\n",
    "版の番号": '{"v": "1.2.3"}\n',
    "ふつうの文": "この Repository は Public です。\n",
    "記号で区切った架空の ID": "camera = TST-CAMERA-0001\n",
}

#: 引数へ取り違えて渡す、 長い架空の値（実行するときに組み立てる）。
LONG_ONE_LINE = " ".join(f"FICT_{i:04d}_CO" for i in range(60))

#: 出力に出てはいけない値（どの試験でも、 出力にこれが無いことを見る）。
NOT_SHOWN = (SITE_IP_192, SITE_IP_10, SITE_IP_172, LINK_LOCAL, SERIAL)

#: 検査と試験を置いた Repository の根（正本でも、 写しを置いた Core でも）。
ROOT = HERE.parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "basic-checks.yml"
#: 再利用 workflow。 正本（この Repository）にだけある。
REUSABLE = WORKFLOWS / "basic-checks-reusable.yml"
STEP = "現場の値が混ざっていないか"


def _hits(body: str, name: str = "zz_probe.md") -> list:
    """一時フォルダに 1 ファイルを置いて `check()` に掛ける。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(body, encoding="utf-8")
        orig, C.ROOT = C.ROOT, Path(td)
        allow, C._ALLOW_CACHE = C._ALLOW_CACHE, {}
        try:
            return C.check(p)
        finally:
            C.ROOT, C._ALLOW_CACHE = orig, allow


def _secret_uses(text: str) -> "list[str]":
    """workflow の中で secret に触る所。 コメントの行は見ない。

    `${{ }}` の式の中の `secrets`（`secrets.X`・`secrets['X']`・`toJSON(secrets)`
    など）、 式の外の `secrets.`・`secrets[`、 `secrets:` の受け渡し
    （`secrets: inherit` など）を拾う。
    """
    body = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
    found = [e for e in re.findall(r"\$\{\{(.*?)\}\}", body, re.S)
             if re.search(r"\bsecrets\b", e, re.I)]
    found += re.findall(r"\bsecrets\s*[.\[]", body, re.I)
    found += re.findall(r"^\s*secrets\s*:", body, re.M)
    return found


def _step(name: str = STEP) -> "list[str]":
    """workflow の 1 つの段の行（コメントを除く）。 PyYAML に頼らない。"""
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.strip() == f"- name: {name}"]
    if len(starts) != 1:
        # 同じ名前の段が 2 つあると、 前の段だけを見て、 後ろの段（env や引数を足した物）を見逃す
        raise AssertionError(f"{WORKFLOW.name} に段「{name}」が 1 つだけあること"
                             f"（見つかった数: {len(starts)}）")
    out = [lines[starts[0]].strip()]
    for ln in lines[starts[0] + 1:]:
        s = ln.strip()
        if s.startswith("- "):
            break
        if s and not s.startswith("#"):
            out.append(s)
    return out


class ItFindsSiteValuesTest(unittest.TestCase):

    def test_site_values_are_found(self) -> None:
        for label, body in FOUND.items():
            with self.subTest(label=label):
                self.assertTrue(_hits(body), f"{label} を見逃しています")

    def test_samples_pass(self) -> None:
        """**見本を落とさないこと。** 落とすと見本が書けなくなる。"""
        for label, body in PASSED.items():
            with self.subTest(label=label):
                self.assertEqual(_hits(body), [], f"{label} を誤検出しています")

    def test_the_line_is_reported(self) -> None:
        """どの行かを返す（値の代わりに、 手元で開いて見るため）。"""
        body = f"一行目\n二行目\nplc = {SITE_IP_10}\nserial = {SERIAL}\n"
        self.assertEqual(_hits(body), [(3, "現場の IP らしきもの"),
                                       (4, "シリアルらしき並び")])

    def test_the_values_are_not_returned(self) -> None:
        """**見つけた値そのものを返さない。** 返すと、 どこかで出力される。"""
        hits = _hits(f"{SITE_IP_192} {SERIAL} {LINK_LOCAL}\n")
        self.assertTrue(hits)
        for v in (SITE_IP_192, SERIAL, LINK_LOCAL):
            with self.subTest(value=v):
                self.assertNotIn(v, repr(hits))


class PathsInTheOutputAreRedactedTest(unittest.TestCase):
    """**出力に書くファイルの道の中の値を、 欠片も残さず伏せる。**

    以前は、 `_` の隣の IP・シリアルや、 制御文字の隣の値が出た（制御文字を先に
    置き換えると、 `\\xNN` の英数字が値の境界を壊した）。
    """

    def test_values_in_file_paths_are_hidden(self) -> None:
        """**出力に書くファイルの道の中の値も伏せる**（IP やシリアルを付けたファイルなど）。"""
        self.assertEqual(C._shown(f"docs/{SITE_IP_192}/{SERIAL}-front/a.md"),
                         "docs/＜IP＞/＜シリアル＞-front/a.md")
        self.assertEqual(C._shown("docs/192.0.2.20/a.md"), "docs/192.0.2.20/a.md",
                         "見本の IP まで伏せています")

    def test_values_next_to_underscores_dots_and_controls(self) -> None:
        cases = {
            f"site_{SITE_IP_192}.md": "site_＜IP＞.md",
            f"host.{SITE_IP_172}.md": "host.＜IP＞.md",
            f"1.{SITE_IP_192}.md": "1.＜IP＞.md",
            f"a{chr(1)}{SITE_IP_10}.md": "a\\x01＜IP＞.md",
            f"cam_{SERIAL}.md": "cam_＜シリアル＞.md",
            f"{SERIAL}_front.md": "＜シリアル＞_front.md",
            f"{chr(11)}{SERIAL}.md": "\\x0b＜シリアル＞.md",
            f"x{chr(10)}{SITE_IP_10}.md": "x\\x0a＜IP＞.md",
            f"tab{chr(9)}name.md": "tab\\x09name.md",
            "docs/192.0.2.20/a.md": "docs/192.0.2.20/a.md",
        }
        for rel, want in cases.items():
            with self.subTest(rel=repr(rel)):
                self.assertEqual(C._shown(rel), want)

    def test_overlapping_values_become_one_mark(self) -> None:
        """重なって見つかった値（IP どうし）は、 1 つにまとめて伏せる。"""
        for rel in (f"{SITE_IP_10}.0.0.1.md", f"{SITE_IP_172}.0.0.1.md"):
            with self.subTest(rel=rel):
                self.assertEqual(C._shown(rel), "＜IP＞.md")

    def test_the_content_check_is_unchanged(self) -> None:
        """道を伏せる形を緩めても、 中身の照合の範囲は変えない（main と同じ）。"""
        self.assertEqual(_hits(f"host_{SITE_IP_10}\n"), [])
        self.assertEqual(_hits(f"cam_{SERIAL}\n"), [])


class _TempRepo:
    """一時の git リポジトリに script を写して流す土台（これ自体は試験を持たない）。"""

    def setUp(self) -> None:
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        self.base = base
        self.repo = base / "repo"
        (self.repo / "scripts" / "ci").mkdir(parents=True)
        shutil.copy2(HERE / "customer_data_check.py", self.repo / "scripts" / "ci")
        (self.repo / "notes.md").write_text("ふつうのメモ\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)

    def run_check(self, *args: str) -> "tuple[int, str]":
        """script を流す。"""
        script = self.repo / "scripts" / "ci" / "customer_data_check.py"
        r = subprocess.run([sys.executable, str(script), *args],
                           cwd=self.repo, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def assertNothingShown(self, out: str) -> None:
        for v in NOT_SHOWN:
            self.assertNotIn(v, out, "値が出力に出ています")

    def assertStopsSafely(self, code: int, out: str, *hidden: str) -> None:
        """終了コード 2 で止まり、 値・場所・OS の例外の文・traceback を出さない。"""
        self.assertEqual(code, 2, out)
        self.assertNothingShown(out)
        for v in (*hidden, str(self.base), "Traceback", "Errno", "FICT_0"):
            self.assertNotIn(v, out)


class TheCommandLineTest(_TempRepo, unittest.TestCase):
    """script を実際に流して、 出力と終了コードを確かめる（一時の git リポジトリ）。"""

    def test_it_says_names_are_not_checked(self) -> None:
        """顧客の名前を照合しないことを、 黙らずに出力する（照合したと思わせない）。"""
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("顧客の名前: 照合しません", out)
        self.assertIn("OK: 現場の値は見つかりませんでした", out)

    def test_removed_registry_options_stop(self) -> None:
        """**外した登録表の引数を渡しても、 合格にしない。** 2 で止まり、 値を出さない。

        前の呼び出し（`--require-registry` など）が残っていると、 名前を照合した
        つもりで合格に見えてしまう（`MirumeAI/.github#209`）。
        """
        value = "FICT_VALUE_0001"
        for args in (("--registry", value), (f"--registry={value}",),
                     ("--require-registry",), ("--allow-registry-in-repo",)):
            with self.subTest(args=args[0].partition("=")[0]):
                code, out = self.run_check(*args)
                self.assertStopsSafely(code, out, value)
                self.assertIn("引数が正しくありません", out)

    def test_argument_errors_do_not_echo_values(self) -> None:
        """引数の誤り（値を位置の引数へ渡すなど）でも、 渡した値を出さない。"""
        for args in (("FICT_VALUE_0001",), (LONG_ONE_LINE,),
                     ("--warn-only=FICT_VALUE_0001",)):
            with self.subTest(n=len(args[0])):
                code, out = self.run_check(*args)
                self.assertStopsSafely(code, out, "FICT_VALUE_0001")
                self.assertIn("引数が正しくありません", out)

    def test_the_allow_file_is_read(self) -> None:
        """`.ci-leak-allow` に書いたファイルは見ない（main が読み込むこと）。

        このリポジトリの除外は 0 件なので、 ここで確かめないと、 読み込みを
        壊しても気づけない。
        """
        (self.repo / "site.md").write_text(f"plc = {SITE_IP_10}\n", encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        (self.repo / C.ALLOW_FILE).write_text("site.md  # 試験のための除外\n",
                                               encoding="utf-8")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("対象外 1", out)

    def test_found_values_are_not_printed(self) -> None:
        """**見つけた値を標準出力にも標準エラーにも出さない。** 種類・ファイル・行だけ。

        ファイルの道に入った値（IP やシリアルを付けたファイルなど）も伏せる。
        """
        (self.repo / "site.md").write_text(
            f"# 現場のメモ\nplc = {SITE_IP_10}\nserial = {SERIAL}\n",
            encoding="utf-8")
        (self.repo / f"{LINK_LOCAL}.md").write_text(f"serial = {SERIAL}\n",
                                                    encoding="utf-8")
        (self.repo / f"cam_{SERIAL}_{SITE_IP_192}.md").write_text(
            f"plc = {SITE_IP_10}\n", encoding="utf-8")
        for extra in ((), ("--warn-only",)):
            with self.subTest(args=extra):
                code, out = self.run_check(*extra)
                self.assertEqual(code, 0 if extra else 1, out)
                for want in ("現場の IP らしきもの  file=site.md  line=2",
                             "シリアルらしき並び  file=site.md  line=3",
                             "シリアルらしき並び  file=＜IP＞.md  line=1",
                             "現場の IP らしきもの  file=cam_＜シリアル＞_＜IP＞.md  line=1"):
                    self.assertIn(want, out)
                self.assertNothingShown(out)

    def test_the_normal_ci_step_runs(self) -> None:
        """**通常の CI の段そのものを流す。** 引数も secret も足さずに流れる。

        この段は PR の code が動く workflow にあるので、 secret を渡さない
        （書き込み権限で workflow を変えれば読み出せる）。
        """
        step = _step()
        self.assertEqual(step, [f"- name: {STEP}",
                                "run: python scripts/ci/customer_data_check.py"],
                         "通常の CI の段に env（secret）や引数を足していないこと")
        args = shlex.split(step[-1].partition("run:")[2])
        # 流すのが、 この検査そのものであること（引数だけを流すと、 段が別の物を流しても気づけない）
        self.assertEqual(args[:2], ["python", "scripts/ci/customer_data_check.py"])
        code, out = self.run_check(*args[2:])
        self.assertEqual(code, 0, out)
        self.assertIn("顧客の名前: 照合しません", out)

    def test_every_tracked_path_is_read(self) -> None:
        """**追跡しているファイルは、 道にどんな文字があっても実際に読む。**

        改行区切りの `git ls-files` は、 日本語・引用符・バックスラッシュ・
        制御文字を含む道を表示用に引用符で囲む。 検査はそれを道として使い、
        存在しないファイルとして黙って飛ばしていた。
        """
        paths = ["ascii/plain.md", "日本語/手順書.md", "with space/file name.md",
                 'quote"double.md', "quote'single.md", "back\\slash.md",
                 "tab\tname.md", "new\nline.md"]
        for rel in paths:
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"plc = {SITE_IP_10}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        # 追跡していない新しいファイル（--others）も同じ
        (self.repo / "未追跡の 手順.md").write_text(f"nas = {SITE_IP_172}\n",
                                                  encoding="utf-8")
        shown = subprocess.run(["git", "ls-files"], cwd=self.repo, capture_output=True,
                               text=True, check=True).stdout
        self.assertIn('"', shown, "git が道を引用符で囲む前提が崩れています")
        orig, C.ROOT = C.ROOT, self.repo
        try:
            got = {str(p.relative_to(self.repo)) for p in C.tracked()}
            for rel in paths + ["未追跡の 手順.md"]:
                with self.subTest(path=rel):
                    self.assertIn(rel, got)
                    self.assertTrue(C.check(self.repo / rel), "中身を読んでいません")
        finally:
            C.ROOT = orig
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        for rel in paths:
            seen = rel.replace("\t", "\\x09").replace("\n", "\\x0a")
            self.assertIn(f"現場の IP らしきもの  file={seen}  line=1", out)
        self.assertIn("現場の IP らしきもの  file=未追跡の 手順.md  line=1", out)
        self.assertNothingShown(out)

    def test_a_path_that_is_not_utf8_is_read(self) -> None:
        """UTF-8 でない道も読み、 出力では表示できる形に直す（落ちない）。"""
        name = os.fsdecode(b"latin1_" + bytes([0xE9]) + b".md")
        try:
            (self.repo / name).write_text(f"plc = {SITE_IP_10}\n", encoding="utf-8")
        except (OSError, UnicodeEncodeError):
            self.skipTest("このファイルシステムは UTF-8 でない名前を作れない")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn(f"file=latin1_{chr(0xFFFD)}.md  line=1", out)


class TheAllowListNeedsAReasonTest(unittest.TestCase):
    """**理由の無い除外を作らないこと。**

    理由が無いと、 次に見た人が外せない。 許可リストは script の中では
    なくファイルに持たせている（Repository ごとに違うため。 script に
    持たせると Repository ごとに分かれ、 改善が届かなくなる）。
    """

    def test_it_reads_paths_and_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / C.ALLOW_FILE).write_text(
                "# 見出しの行\n\n"
                "a/b.md   # 理由をここに書く\n"
                "c/d.py\n",
                encoding="utf-8")
            got = C._allow(root)
            self.assertEqual(got["a/b.md"], "理由をここに書く")
            self.assertIn("理由が書かれていません", got["c/d.py"])
            self.assertEqual(len(got), 2, "見出しや空行を拾っています")

    def test_no_allow_file_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(C._allow(Path(td)), {})

    def test_this_repository_has_reasons_for_every_entry(self) -> None:
        for path, why in C._allow(ROOT).items():
            with self.subTest(path=path):
                self.assertNotIn("理由が書かれていません", why)

    def test_every_entry_points_at_a_real_file(self) -> None:
        """**実在しないパスを対象外にしないこと。**

        Core で、 その Repository に無いパスが一覧に入っていた（別の Repository の
        一覧をそのまま写していた）。 先に書いてあると、 後でそのパスを作った人の
        混入が黙って通る。
        """
        missing = [p for p in C._allow(ROOT) if not (ROOT / p).is_file()]
        self.assertEqual(missing, [], f"実在しないのに対象外です: {missing}")

    def test_the_check_and_its_test_are_not_exempted(self) -> None:
        """**検査とその試験を除外しない。** 除外すると、 そこに書いた実在の値を
        検査が見逃す（試験の値は架空にし、 見つけるべき値は実行時に組み立てる）。"""
        got = C._allow(ROOT)
        for f in ("scripts/ci/customer_data_check.py", "scripts/ci/test_customer_data.py"):
            with self.subTest(file=f):
                self.assertNotIn(f, got)


class ItRunsOnThisRepositoryTest(unittest.TestCase):
    """検査と試験を置いた Repository（正本でも、 写しを置いた Core でも）の木で流す。"""

    def test_the_current_contents_pass(self) -> None:
        """**追跡しているファイルに、 現場の値が無いこと**（正本は Public に置いてよい状態）。

        **検査を本当に起動して見る。** `check()` を直に呼ぶと、 対象外の一覧を
        読み込む前の状態を見ることになり、 許可したはずのファイルで落ちる。 逆に、
        読み込みを忘れた実装も見逃す。 入口ごと通せば、 どちらも起きない。
        """
        r = subprocess.run([sys.executable,
                            str(HERE / "customer_data_check.py")],
                           capture_output=True, text=True,
                           cwd=str(ROOT))
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-600:])

    def test_a_brand_new_file_is_checked_before_add(self) -> None:
        """**`git add` する前の新しいファイルも検査に入ること。**

        Core で、 `git ls-files` だけを見ていた間は、 新しく作ったファイルを
        `git add` するまで検査しなかった。 手元で「OK」と出たものが、 commit した
        後の CI で初めて落ちた（新しく作った説明文に顧客名を書いていた）。

        木の根に一時のファイルを置くので、 落ちても必ず消す（addCleanup）。
        """
        probe = ROOT / "zz_leak_probe_for_test.py"
        self.assertFalse(probe.exists(), "同じ名前のファイルが既にあります")
        self.addCleanup(probe.unlink, missing_ok=True)
        probe.write_text(f"# {SITE_IP_10} のこと\n", encoding="utf-8")
        files = [str(f.relative_to(ROOT)) for f in C.tracked()]
        self.assertIn(probe.name, files,
                      "add していない新しいファイルが検査に入っていません")
        self.assertTrue(C.check(probe), "中身を見ていません")

    def test_the_workflow_calls_it(self) -> None:
        """**この Repository の workflow が呼ぶこと。 再利用 workflow には入れないこと。**

        再利用 workflow は顧客 Repository でも走る。 顧客 Repository の `config/` には
        PLC の IP が正当に入るので、 そこで走らせると毎回 NG になり、 誰も見なくなる。

        再利用 workflow は正本にだけある。 写しを置いた Core には無いので、 そこでは
        この Repository の workflow が呼ぶことだけを見る（飛ばさない）。
        """
        own = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("customer_data_check.py", own,
                      "本 Repository の workflow が呼んでいません")
        if not REUSABLE.is_file():
            return
        reusable = REUSABLE.read_text(encoding="utf-8")
        self.assertNotIn("customer_data_check.py", reusable,
                         "再利用 workflow に入れると顧客 Repository で"
                         "毎回 NG になります")

    def test_no_workflow_gets_secrets_or_privileged_events(self) -> None:
        """**PR の code が動く workflow に、 secret を渡さない。**

        書き込み権限があれば、 workflow や試験を書き換えて secret を読み出せる。
        この Repository の workflow は secret を一切使わない（Release を作る
        workflow があっても `github.token` だけ）。 特権のイベント
        （pull_request_target・workflow_run）も、 信頼の境界を決めて試験ごと
        足すまでは使わない。
        """
        workflows = sorted(WORKFLOWS.glob("*.y*ml"))
        # 置き場所を取り違えて 0 件になり、 何も見ずに通ることのないように
        self.assertIn(WORKFLOW, workflows)
        for wf in workflows:
            text = wf.read_text(encoding="utf-8")
            with self.subTest(workflow=wf.name):
                self.assertEqual(_secret_uses(text), [])
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("workflow_run", text)

    def test_the_secret_check_sees_every_form(self) -> None:
        """secret の書き方の違い（`secrets.X`・`secrets['X']`・`toJSON(secrets)`・
        `secrets: inherit` など）を、 どれも見つける。 見つけ損ねると、 通常の CI へ
        secret を足せてしまう。"""
        used = ["x: ${{ secrets.X }}", "x: ${{ secrets['X'] }}", 'x: ${{ secrets["X"] }}',
                "x: ${{ secrets[format('{0}', 'X')] }}", "x: ${{ toJSON(secrets) }}",
                "x: ${{\n  secrets.X\n}}", "run: echo ${{ secrets . X }}",
                "x: ${{ SECRETS.X }}", "    secrets: inherit", "    secrets:\n      X: y"]
        for snippet in used:
            with self.subTest(snippet=snippet):
                self.assertTrue(_secret_uses(snippet), "secret の使い方を見逃しています")
        unused = ["# secrets.X は使わない", "x: ${{ github.token }}",
                  "- name: secret を使わない段", "x: ${{ env.MY_SECRETS }}"]
        for snippet in unused:
            with self.subTest(snippet=snippet):
                self.assertEqual(_secret_uses(snippet), [])


if __name__ == "__main__":
    unittest.main()

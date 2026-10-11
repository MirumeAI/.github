#!/usr/bin/env python3
"""顧客の値の混入検査が働くことを確かめる。

**このリポジトリは Public。** それなのに混入検査が無かった。 Private な
Core 2 件にはあり、 **必要な順番が逆になっていた**。

Private 側では実際に 3 回混入した。 どれも説明文・テストの見本・コメントに
顧客名を書いたもので、 **書いた本人は混入だと思っていなかった**。

**試験の値はすべて架空。** 検査や試験に実在の値を書くと、 検査とこの試験を
`.ci-leak-allow` で除外しないと通らなくなり、 除外がその値を検査から隠す。
そうならないように:

  ・顧客の名前は、 リポジトリの外の登録表から読む（script に書かない）
  ・試験の登録表は架空の名前で、 実行するときに一時フォルダか環境変数で渡す
  ・見つけるべき値（私用の IP・シリアルらしい並び）は実行するときに組み立てる。
    このファイル自体は検査を通るので、 除外の一覧に頼らない

**見つけた値は出力しない**（種類・ファイル・行だけ）。 **通常の CI には
登録表を渡さない**（PR の code が動くので、 書き込み権限で secret を読み
出せてしまう）。 **追跡しているファイルは、 道にどんな文字があっても読む。**
どれもここで確かめる。

見本は通す。 `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に予約された
アドレスで、 繋ぎに行っても届かない。
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import customer_data_check as C                            # noqa: E402

#: 架空の値。 **このファイルに検査が拾う形で書かない**（実行するときに組み立てる）。
SITE_IP_192 = "192.168." + "0.10"
SITE_IP_10 = "10." + "0.0.10"
SITE_IP_172 = "172.16." + "0.10"
LINK_LOCAL = "169.254." + "0.10"
SERIAL = "TST" + "00000001"

#: 見つかるべき値（名前は下の登録表の試験で見る）。
FOUND = {
    "現場の IP（192.168）": f"host = {SITE_IP_192}\n",
    "現場の IP（10.x）": f"plc = {SITE_IP_10}\n",
    "現場の IP（172.16-31）": f"nas = {SITE_IP_172}\n",
    "リンクローカル": f"cam = {LINK_LOCAL}\n",
    "シリアルらしい並び": f"serial = {SERIAL}\n",
}

#: 通すべき値。
PASSED = {
    "文書用の予約 IP": "host = 192.0.2.20\n",
    "別の予約 IP": "host = 198.51.100.7\n",
    "ループバック": "host = 127.0.0.1\n",
    "版の番号": '{"v": "1.2.3"}\n',
    "ふつうの文": "この Repository は Public です。\n",
    "記号で区切った架空の ID": "camera = TST-CAMERA-0001\n",
}

#: 架空の顧客の登録表（試験のときだけ一時フォルダか環境変数で渡す）。
REGISTRY = (
    "# 架空の顧客の登録表（試験用）\n"
    "\n"
    "EXAMPLE_CUSTOMER\n"
    "fact   # 英字だけの名前は語として探す\n"
    "架空精機\n"
)

#: どこにも無い架空の語（照合して 0 件になる登録表に使う。 実行するときに組み立てる）。
NOWHERE = "ZZQX" + "_NOWHERE_" + "QXZZ"

#: 場所の欄へ取り違えて渡す、 長い架空の登録表（実行するときに組み立てる）。
#: 長い値を場所として触ると、 OS の例外の文（名前が長すぎる）に値が出ていた。
LONG_REGISTRY = REGISTRY + "".join(f"FICT_{i:04d}_CO\n" for i in range(300))
LONG_ONE_LINE = " ".join(f"FICT_{i:04d}_CO" for i in range(60))

#: 出力に出てはいけない値（どの試験でも、 出力にこれが無いことを見る）。
NOT_SHOWN = (SITE_IP_192, SITE_IP_10, SITE_IP_172, LINK_LOCAL, SERIAL,
             "EXAMPLE_CUSTOMER", "架空精機")

WORKFLOW = HERE.parents[1] / ".github" / "workflows" / "basic-checks.yml"
STEP = "現場の値が混ざっていないか"


def _b64(text: str) -> str:
    """環境変数（CI では secret）で渡す形: `base64:` + 登録表を base64 にした 1 行。"""
    return C.REGISTRY_ENV_PREFIX + base64.b64encode(text.encode("utf-8")).decode("ascii")


def _registry(where: Path, text: str = REGISTRY) -> Path:
    p = where / "customer_registry.txt"
    p.write_text(text, encoding="utf-8")
    return p


def _hits(body: str, registry: "tuple" = (), name: str = "zz_probe.md") -> list:
    """一時フォルダに 1 ファイルを置いて `check()` に掛ける。"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / name
        p.write_text(body, encoding="utf-8")
        orig, C.ROOT = C.ROOT, Path(td)
        allow, C._ALLOW_CACHE = C._ALLOW_CACHE, {}
        try:
            return C.check(p, registry)
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
    start = next(i for i, ln in enumerate(lines) if ln.strip() == f"- name: {name}")
    out = [lines[start].strip()]
    for ln in lines[start + 1:]:
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


class CustomerNamesComeFromTheRegistryTest(unittest.TestCase):
    """**顧客の名前は script に書かない。** リポジトリの外の登録表から読む。"""

    def setUp(self) -> None:
        self.td = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.td, True)
        # 登録表はリポジトリ（repo）の外に置く
        self.repo = self.td / "repo"
        self.repo.mkdir()
        self.names = C.load_registry(_registry(self.td), root=self.repo)

    def test_the_script_holds_no_names(self) -> None:
        """登録表を渡さなければ、 名前を 1 つも見つけない（script に名前が無い）。"""
        self.assertEqual(C._REGISTRY, (), "読み込んだだけで名前を持っています")
        for body in ("担当: EXAMPLE_CUSTOMER\n", "顧客: 架空精機\n"):
            with self.subTest(body=body):
                self.assertEqual(_hits(body), [])

    def test_registered_names_are_found(self) -> None:
        cases = {
            "英字の名前": "# EXAMPLE_CUSTOMER の設定\n",
            "大文字と小文字の違い": "dir = example_customer/\n",
            "日本語の名前": "顧客: 架空精機株式会社\n",
            "語としての一致": "FACT: 値\n",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                self.assertTrue(_hits(body, self.names), f"{label} を見逃しています")

    def test_a_name_inside_another_word_is_not_found(self) -> None:
        """**部分一致で拾わない。** 以前は実在の名前で 6 ファイルを誤検出した。"""
        for body in ("factory = 1\n", "artifact\n", "TEST_FACTORY\n"):
            with self.subTest(body=body):
                self.assertEqual(_hits(body, self.names), [])

    def test_the_name_itself_is_not_reported(self) -> None:
        """**見つかった名前を出力に書かない。** CI の記録は外から見えることがある。"""
        hits = _hits("前の行\nEXAMPLE_CUSTOMER と 架空精機\n", self.names)
        self.assertEqual(hits, [(2, "顧客に固有の語（登録表の 3 行目）"),
                                (2, "顧客に固有の語（登録表の 5 行目）")])
        self.assertNotIn("EXAMPLE_CUSTOMER", repr(hits))
        self.assertNotIn("架空精機", repr(hits))

    def test_the_name_is_not_in_repr(self) -> None:
        """例外の表示などから、 名前が記録に残らないこと。"""
        self.assertNotIn("EXAMPLE_CUSTOMER", repr(C.Name(3, "EXAMPLE_CUSTOMER")))
        self.assertNotIn("EXAMPLE_CUSTOMER", repr(self.names))

    def test_it_loads_without_registering_the_module(self) -> None:
        """importlib で直に読んでも落ちないこと（`Name` を dataclass にすると落ちる）。"""
        spec = importlib.util.spec_from_file_location(
            "customer_data_check_probe", HERE / "customer_data_check.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        self.assertEqual(repr(m.Name(1, "EXAMPLE_CUSTOMER")), "Name(line=1)")

    def test_the_environment_value_is_one_line_of_base64(self) -> None:
        """環境変数（CI では secret）は `base64:` + 1 行。 **それ以外は読まずに止める。**

        複数行の secret は GitHub が 1 行ずつも伏せ字にし、 公開の記録の
        伏せ字の位置から名前を推し量れてしまう。 中身をそのまま貼る
        取り違えも、 黙って通さない。
        """
        got = C.decode_registry(_b64(REGISTRY))
        self.assertEqual([(n.line, n.text) for n in got],
                         [(3, "EXAMPLE_CUSTOMER"), (4, "fact"), (5, "架空精機")])
        one = _b64(REGISTRY)
        cases = {
            "base64 にせず貼った": REGISTRY,
            "base64 の文字でない": C.REGISTRY_ENV_PREFIX + "!!!",
            "折り返した base64": one[:20] + "\n" + one[20:],
            "語が無い": _b64("# 説明だけ\n"),
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(C.RegistryError) as cm:
                    C.decode_registry(value)
                for v in ("EXAMPLE_CUSTOMER", "架空精機"):
                    self.assertNotIn(v, str(cm.exception))
        # 印が無ければ、 中身を読もうとせずに止める（たまたま base64 として読める値でも）
        with self.assertRaises(C.RegistryError) as cm:
            C.decode_registry(one[len(C.REGISTRY_ENV_PREFIX):])
        self.assertIn(f"{C.REGISTRY_ENV_PREFIX} で始まっていません", str(cm.exception))

    def test_the_registry_format(self) -> None:
        """1 行に 1 つ。 `#` から後と空行は読まない。 行番号を覚える。"""
        self.assertEqual([(n.line, n.text) for n in self.names],
                         [(3, "EXAMPLE_CUSTOMER"), (4, "fact"), (5, "架空精機")])
        # 環境変数で渡すとき（BOM・CRLF が付いていても読む）
        got = C.parse_registry("\ufeffEXAMPLE_CUSTOMER\r\n# 説明\r\nfact\r\n")
        self.assertEqual([(n.line, n.text) for n in got],
                         [(1, "EXAMPLE_CUSTOMER"), (3, "fact")])

    def test_an_empty_registry_is_refused(self) -> None:
        """名前が 1 つも無い登録表は使わない（照合したつもりにさせない）。"""
        empty = self.td / "empty"
        empty.mkdir()
        with self.assertRaises(C.RegistryError):
            C.load_registry(_registry(empty, "# 名前が無い\n\n"), root=self.repo)

    def test_a_missing_registry_is_refused(self) -> None:
        with self.assertRaises(C.RegistryError):
            C.load_registry(self.td / "none.txt", root=self.repo)

    def test_a_registry_inside_the_repository_is_refused(self) -> None:
        """**登録表をリポジトリの中に置かない。** commit されると名前が外へ出る。"""
        inside = _registry(self.repo)
        with self.assertRaises(C.RegistryError):
            C.load_registry(inside, root=self.repo)
        # 非公開のリポジトリで置くと決めた場合だけ、 明示すれば使える
        self.assertTrue(C.load_registry(inside, root=self.repo, allow_in_repo=True))

    def test_errors_show_neither_the_place_nor_the_contents(self) -> None:
        """**エラーの文に、 登録表の場所も中身も入れない。**

        中身を場所の欄へ渡す取り違え（secret を場所の環境変数に入れるなど）
        でも、 名前が記録に出ないように。
        """
        named = self.td / "EXAMPLE_CUSTOMER"
        named.mkdir()
        loop = named / "loop"
        os.symlink(loop, loop)
        cases = {
            "見つからない": lambda: C.load_registry(named / "none.txt", root=self.repo),
            "中身を場所として渡した": lambda: C.load_registry(REGISTRY, root=self.repo),
            "長い中身を場所として渡した":
                lambda: C.load_registry(LONG_ONE_LINE, root=Path("/nonexistent")),
            "NUL を含む場所": lambda: C.load_registry("EXAMPLE_CUSTOMER" + chr(0),
                                                    root=self.repo),
            "base64 の値を場所として渡した": lambda: C.load_registry(_b64(REGISTRY),
                                                             root=self.repo),
            "ディレクトリ": lambda: C.load_registry(named, root=self.repo),
            "symlink のループ": lambda: C.load_registry(loop, root=self.repo),
            "空": lambda: C.load_registry(_registry(named, "# 無い\n"), root=self.repo),
            "リポジトリの中": lambda: C.load_registry(_registry(self.repo), root=self.repo),
        }
        for label, call in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(C.RegistryError) as cm:
                    call()
                for v in ("EXAMPLE_CUSTOMER", "架空精機", "FICT_0", str(self.td), "Errno"):
                    self.assertNotIn(v, str(cm.exception))
                self.assertIsNone(cm.exception.__cause__)

    def test_values_in_file_paths_are_hidden(self) -> None:
        """**出力に書くファイルの道の中の値も伏せる**（顧客の名前を付けたファイルなど）。"""
        orig, C._REGISTRY = C._REGISTRY, self.names
        try:
            shown = C._shown(f"docs/{SITE_IP_192}/{SERIAL}-EXAMPLE_CUSTOMER/架空精機.md")
        finally:
            C._REGISTRY = orig
        self.assertEqual(shown, "docs/＜IP＞/＜シリアル＞-＜登録表の 3 行目＞/"
                                "＜登録表の 5 行目＞.md")
        self.assertEqual(C._shown("docs/192.0.2.20/a.md"), "docs/192.0.2.20/a.md",
                         "見本の IP まで伏せています")


class PathsInTheOutputAreRedactedTest(unittest.TestCase):
    """**出力に書くファイルの道の中の値を、 欠片も残さず伏せる。**

    以前は、 `_` の隣の IP・シリアル、 制御文字の隣の名前、 重なる語、 NFD の
    道で値が出た（制御文字を先に置き換えると、 `\\xNN` の英数字が語の境界を
    壊した）。
    """

    def shown(self, rel: str, registry: str = REGISTRY) -> str:
        orig, C._REGISTRY = C._REGISTRY, C.parse_registry(registry)
        try:
            return C._shown(rel)
        finally:
            C._REGISTRY = orig

    def test_values_next_to_underscores_dots_and_controls(self) -> None:
        cases = {
            f"site_{SITE_IP_192}.md": "site_＜IP＞.md",
            f"host.{SITE_IP_172}.md": "host.＜IP＞.md",
            f"1.{SITE_IP_192}.md": "1.＜IP＞.md",
            f"a{chr(1)}{SITE_IP_10}.md": "a\\x01＜IP＞.md",
            f"cam_{SERIAL}.md": "cam_＜シリアル＞.md",
            f"{SERIAL}_front.md": "＜シリアル＞_front.md",
            f"{chr(11)}EXAMPLE_CUSTOMER.md": "\\x0b＜登録表の 3 行目＞.md",
            f"x{chr(10)}EXAMPLE_CUSTOMER.md": "x\\x0a＜登録表の 3 行目＞.md",
            f"tab{chr(9)}name.md": "tab\\x09name.md",
            "docs/192.0.2.20/a.md": "docs/192.0.2.20/a.md",
        }
        for rel, want in cases.items():
            with self.subTest(rel=repr(rel)):
                self.assertEqual(self.shown(rel), want)

    def test_overlapping_terms_leave_no_fragments(self) -> None:
        """長い語と短い語、 一部だけ重なる語。 登録の順に関係なく、 欠片を残さない。"""
        reg = "EXAMPLE\nEXAMPLE_CUSTOMER\n架空\n架空精機\nORBIT DYN\nDYN WORKS\n"
        self.assertEqual(self.shown("EXAMPLE_CUSTOMER/架空精機.md", reg),
                         "＜登録表の 2 行目＞/＜登録表の 4 行目＞.md")
        self.assertEqual(self.shown("ORBIT DYN WORKS.md", reg), "＜登録表の 5 行目＞.md")
        rev = "".join(ln + "\n" for ln in reversed(reg.splitlines()))
        for rel in ("EXAMPLE_CUSTOMER/架空精機.md", "ORBIT DYN WORKS.md"):
            with self.subTest(rel=rel):
                got = self.shown(rel, rev)
                for piece in ("EXAMPLE", "CUSTOMER", "架空", "精機", "ORBIT", "DYN", "WORKS"):
                    self.assertNotIn(piece, got)

    def test_nfd_paths_are_hidden(self) -> None:
        """道と登録表で Unicode の形（NFC と NFD）が違っても伏せる。"""
        name = "架空ガス"
        nfd = unicodedata.normalize("NFD", name)
        self.assertNotEqual(nfd, name, "試験の前提（濁点が分かれる）が崩れています")
        self.assertEqual(self.shown(f"docs/{nfd}.md", f"{name}\n"),
                         "docs/＜登録表の 1 行目＞.md")
        self.assertEqual(self.shown(f"docs/{name}.md", f"{nfd}\n"),
                         "docs/＜登録表の 1 行目＞.md")

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
        (self.repo / "notes.md").write_text("担当: EXAMPLE_CUSTOMER\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        self.outside = base / "outside"
        self.outside.mkdir()
        # 決まった場所（~/.config/mirumeai）は、 この試験の中だけの HOME で見る
        self.home = base / "home"
        self.home.mkdir()

    def run_check(self, *args: str, env: "dict | None" = None,
                  cwd: "Path | None" = None) -> "tuple[int, str]":
        """script を流す。 `cwd` を渡すと、 そこから流す（リポジトリの外からなど）。"""
        e = dict(os.environ, HOME=str(self.home))
        e.pop(C.REGISTRY_ENV, None)
        e.pop(C.REGISTRY_FILE_ENV, None)
        e.update(env or {})
        script = self.repo / "scripts" / "ci" / "customer_data_check.py"
        r = subprocess.run([sys.executable, str(script), *args],
                           cwd=cwd or self.repo, capture_output=True, text=True, env=e)
        return r.returncode, r.stdout + r.stderr

    def assertNothingShown(self, out: str) -> None:
        for v in NOT_SHOWN:
            self.assertNotIn(v, out, "値が出力に出ています")

    def assertStopsSafely(self, code: int, out: str, *hidden: str) -> None:
        """終了コード 2 で止まり、 値・場所・OS の例外の文・traceback を出さない。"""
        self.assertEqual(code, C.EXIT_REGISTRY, out)
        self.assertNothingShown(out)
        for v in (*hidden, str(self.base), "Traceback", "Errno", "FICT_0"):
            self.assertNotIn(v, out)


class TheCommandLineTest(_TempRepo, unittest.TestCase):
    """script を実際に流して、 登録表の探し方と出力を確かめる（一時の git リポジトリ）。"""

    def test_without_a_registry_it_says_names_were_not_checked(self) -> None:
        """手元では **黙って合格にしない。** 照合していないことを出力して 0 で終わる。"""
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("登録表が無いので照合していません", out)

    def test_it_can_be_made_to_require_the_registry(self) -> None:
        code, out = self.run_check("--require-registry")
        self.assertEqual(code, C.EXIT_REGISTRY, out)

    def test_a_registry_finds_the_name_without_printing_it(self) -> None:
        code, out = self.run_check("--registry", str(_registry(self.outside)))
        self.assertEqual(code, 1, out)
        self.assertIn("顧客に固有の語（登録表の 3 行目）  file=notes.md  line=1", out)
        self.assertNothingShown(out)

    def test_the_file_environment_variable_is_read(self) -> None:
        code, out = self.run_check(
            env={C.REGISTRY_FILE_ENV: str(_registry(self.outside))})
        self.assertEqual(code, 1, out)
        self.assertIn(f"環境変数 {C.REGISTRY_FILE_ENV} の登録表の 3 件", out)

    def test_the_contents_can_come_from_the_environment(self) -> None:
        """CI は secret の中身を環境変数で渡す（ファイルに書き出さない）。"""
        code, out = self.run_check("--require-registry",
                                   env={C.REGISTRY_ENV: _b64(REGISTRY)})
        self.assertEqual(code, 1, out)
        self.assertIn(f"環境変数 {C.REGISTRY_ENV} の登録表の 3 件", out)
        self.assertNothingShown(out)
        self.assertNotIn(_b64(REGISTRY)[len(C.REGISTRY_ENV_PREFIX):], out)

    def test_contents_given_as_a_place_are_not_printed(self) -> None:
        """中身を場所の欄へ渡しても、 止まるだけで中身を出さない。"""
        for args, env in ((("--registry", REGISTRY), None),
                          ((), {C.REGISTRY_FILE_ENV: REGISTRY})):
            with self.subTest(args=bool(args)):
                code, out = self.run_check(*args, env=env)
                self.assertEqual(code, C.EXIT_REGISTRY, out)
                self.assertNothingShown(out)

    def test_the_default_place_is_read(self) -> None:
        place = self.home / ".config" / "mirumeai"
        place.mkdir(parents=True)
        _registry(place)
        code, out = self.run_check()
        self.assertEqual(code, 1, out)

    def test_a_given_registry_that_is_missing_stops(self) -> None:
        """指定したのに無いときは止める（黙って飛ばさない）。"""
        code, out = self.run_check("--registry", str(self.outside / "none.txt"))
        self.assertEqual(code, C.EXIT_REGISTRY, out)

    def test_a_registry_inside_the_repository_stops(self) -> None:
        code, out = self.run_check("--registry", str(_registry(self.repo)))
        self.assertEqual(code, C.EXIT_REGISTRY, out)
        self.assertNothingShown(out)

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

        ファイルの道に入った値（顧客の名前を付けたファイルなど）も伏せる。
        """
        (self.repo / "site.md").write_text(
            f"# 現場のメモ\nplc = {SITE_IP_10}\nserial = {SERIAL}\n担当: 架空精機\n",
            encoding="utf-8")
        (self.repo / f"{LINK_LOCAL}.md").write_text(f"serial = {SERIAL}\n",
                                                    encoding="utf-8")
        (self.repo / "EXAMPLE_CUSTOMER_notes.md").write_text(
            f"nas = {SITE_IP_172}\n", encoding="utf-8")
        (self.repo / f"cam_{SERIAL}_{SITE_IP_192}.md").write_text(
            f"plc = {SITE_IP_10}\n", encoding="utf-8")
        for extra in ((), ("--warn-only",)):
            with self.subTest(args=extra):
                code, out = self.run_check("--registry", str(_registry(self.outside)),
                                           *extra)
                self.assertEqual(code, 0 if extra else 1, out)
                for want in ("現場の IP らしきもの  file=site.md  line=2",
                             "シリアルらしき並び  file=site.md  line=3",
                             "顧客に固有の語（登録表の 5 行目）  file=site.md  line=4",
                             "シリアルらしき並び  file=＜IP＞.md  line=1",
                             "現場の IP らしきもの  file=＜登録表の 3 行目＞_notes.md  line=1",
                             "現場の IP らしきもの  file=cam_＜シリアル＞_＜IP＞.md  line=1"):
                    self.assertIn(want, out)
                self.assertNothingShown(out)

    def test_the_normal_ci_step_runs_without_the_registry(self) -> None:
        """**通常の CI の段そのものを流す。** 登録表なしで流れ、 そう出力する。

        この段は PR の code が動く workflow にあるので、 secret を渡さない
        （書き込み権限で workflow を変えれば読み出せる）。
        """
        step = _step()
        self.assertEqual(step, [f"- name: {STEP}",
                                "run: python scripts/ci/customer_data_check.py"],
                         "通常の CI の段に env（secret）や引数を足していないこと")
        args = shlex.split(step[-1].partition("run:")[2])
        code, out = self.run_check(*args[2:])
        self.assertEqual(code, 0, out)
        self.assertIn("登録表が無いので照合していません", out)

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


class TheRegistryInputIsCheckedTest(_TempRepo, unittest.TestCase):
    """**登録表を誤って渡しても、 中身・場所・OS の例外の文を出さずに 2 で止まる。**

    以前は、 中身を `--registry` へ取り違えて渡すと、 リポジトリの外から流した
    ときに OS の例外（名前が長すぎる）の文として中身が stderr に出た。 直下から
    流すと「リポジトリの中にあります」と、 別の理由を案内していた。
    """

    #: 場所の欄へ取り違えて渡す中身（どれも、 ファイルに触る前に止める）。
    #: NUL は argv と環境変数に入れられないので、 関数を直に呼ぶ試験で見る。
    CONTENTS = {
        "短い中身（改行入り）": REGISTRY,
        "非常に長い中身": LONG_REGISTRY,
        "改行の無い長い中身": LONG_ONE_LINE,
        "区切りの多い長い値": "/".join(f"FICT_{i:04d}_CO" for i in range(120)),
        "base64: で始まる値": _b64(REGISTRY),
        "# で始まる値": "# 架空の登録表 EXAMPLE_CUSTOMER",
        "制御文字を含む値": "EXAMPLE_CUSTOMER" + chr(1) + "架空精機",
    }

    def test_contents_given_as_the_place_stop_safely(self) -> None:
        """引数でも場所の環境変数でも、 リポジトリの直下からでも外からでも、
        `--allow-registry-in-repo` を付けても、 中身を出さずに 2 で止まる。"""
        payload = _b64(REGISTRY)[len(C.REGISTRY_ENV_PREFIX):]
        for label, value in self.CONTENTS.items():
            for cwd in (self.repo, self.outside):
                for extra in ((), ("--allow-registry-in-repo",)):
                    for how, args, env in (("引数", ("--registry", value), None),
                                           ("環境変数", (), {C.REGISTRY_FILE_ENV: value})):
                        with self.subTest(case=label, cwd=cwd.name, extra=bool(extra),
                                          how=how):
                            code, out = self.run_check(*args, *extra, env=env, cwd=cwd)
                            self.assertStopsSafely(code, out, payload)
                            self.assertIn("場所が正しくありません", out)

    def test_unreadable_or_invalid_files_stop_safely(self) -> None:
        """無い・ディレクトリ・symlink のループ・読めない・入れない場所。 2 で止まり、
        場所も OS の例外の文も出さない。"""
        named = self.outside / "EXAMPLE_CUSTOMER_dir"
        named.mkdir()
        loop_a, loop_b = named / "loop_a", named / "loop_b"
        os.symlink(loop_b, loop_a)
        os.symlink(loop_a, loop_b)
        cases = {"無い": named / "none.txt", "ディレクトリ": named, "symlink のループ": loop_a}
        if os.geteuid() != 0:                  # root は権限に関係なく読めてしまう
            unreadable = _registry(named)
            unreadable.chmod(0)
            self.addCleanup(unreadable.chmod, 0o600)
            locked = named / "locked"
            locked.mkdir()
            _registry(locked)
            locked.chmod(0)
            self.addCleanup(locked.chmod, 0o700)
            cases.update({"読めないファイル": unreadable,
                          "入れない場所の中": locked / "customer_registry.txt"})
        for label, path in cases.items():
            for cwd in (self.repo, self.outside):
                with self.subTest(case=label, cwd=cwd.name):
                    code, out = self.run_check("--registry", str(path), cwd=cwd)
                    self.assertStopsSafely(code, out, "EXAMPLE_CUSTOMER_dir")
                    self.assertIn("読めません", out)
                    self.assertNotIn("リポジトリの中", out)
                    if label == "ディレクトリ":
                        self.assertIn("ファイルではありません", out)

    def test_a_registry_with_a_bad_format_stops(self) -> None:
        """大きすぎる登録表・UTF-8 でない登録表も、 中身を出さずに 2 で止まる。"""
        big = self.outside / "big_registry.txt"
        big.write_text("EXAMPLE_CUSTOMER\n" + "#" * C._MAX_REGISTRY_BYTES, encoding="utf-8")
        latin = self.outside / "latin_registry.txt"
        latin.write_bytes(b"EXAMPLE_CUSTOMER\n" + bytes([0xE9, 0xFF]) + b"\n")
        for path, why in ((big, "大きすぎます"), (latin, "UTF-8 ではありません")):
            with self.subTest(case=why):
                code, out = self.run_check("--registry", str(path))
                self.assertStopsSafely(code, out)
                self.assertIn("形式が正しくありません", out)
                self.assertIn(why, out)

    def test_inside_the_repository_is_said_only_for_real_files(self) -> None:
        """「リポジトリの中にあります」は、 そこに本当にファイルがあるときだけ言う。"""
        code, out = self.run_check("--registry", "missing_registry.txt")
        self.assertStopsSafely(code, out)
        self.assertIn("読めません", out)
        self.assertNotIn("リポジトリの中", out)
        code, out = self.run_check("--registry", str(_registry(self.repo)))
        self.assertStopsSafely(code, out)
        self.assertIn("リポジトリの中にあります", out)

    def test_an_empty_registry_argument_stops(self) -> None:
        """`--registry ""` は「指定されなかった」にしない。 ほかの探し方へ進まず 2 で止まる。"""
        place = self.home / ".config" / "mirumeai"
        place.mkdir(parents=True)
        _registry(place)
        env = {C.REGISTRY_FILE_ENV: str(_registry(self.outside)),
               C.REGISTRY_ENV: _b64(REGISTRY)}
        for value in ("", "   "):
            with self.subTest(value=repr(value)):
                code, out = self.run_check("--registry", value, env=env)
                self.assertStopsSafely(code, out)
                self.assertIn("引数 --registry の登録表の場所が正しくありません", out)

    def test_argument_errors_do_not_echo_values(self) -> None:
        """引数の誤り（中身を位置の引数へ渡すなど）でも、 渡した値を出さない。"""
        for args in ((REGISTRY,), (LONG_ONE_LINE,), (f"--require-registry={REGISTRY}",)):
            with self.subTest(n=len(args[0])):
                code, out = self.run_check(*args)
                self.assertStopsSafely(code, out)
                self.assertIn("引数が正しくありません", out)

    def test_an_unreadable_default_place_stops(self) -> None:
        """決まった場所を確かめられないとき（権限が無いなど）も、 場所を出さずに止める。"""
        if os.geteuid() == 0:
            self.skipTest("root は権限に関係なく読めてしまう")
        place = self.home / ".config" / "mirumeai"
        place.mkdir(parents=True)
        _registry(place)
        place.chmod(0)
        self.addCleanup(place.chmod, 0o700)
        code, out = self.run_check()
        self.assertStopsSafely(code, out)
        self.assertIn("決まった場所の登録表を読めません", out)

    def test_unexpected_errors_stop_without_details(self) -> None:
        """登録表の読み込みで予期しない誤りが起きても、 文を出さずに 2 で止まる。"""
        def boom(*_args: object, **_kwargs: object) -> None:
            raise ValueError(f"{REGISTRY} {self.base}")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(C, "resolve_registry", boom), \
                mock.patch.object(C, "_ALLOW_CACHE", {}), \
                mock.patch.object(sys, "argv", ["customer_data_check.py"]), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = C.main()
        self.assertStopsSafely(code, out.getvalue() + err.getvalue())


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
        for path, why in C._allow(HERE.parents[1]).items():
            with self.subTest(path=path):
                self.assertNotIn("理由が書かれていません", why)

    def test_the_check_and_its_test_are_not_exempted(self) -> None:
        """**検査とその試験を除外しない。** 除外すると、 そこに書いた実在の値を
        検査が見逃す（試験の値は架空にし、 見つけるべき値は実行時に組み立てる）。"""
        got = C._allow(HERE.parents[1])
        for f in ("scripts/ci/customer_data_check.py", "scripts/ci/test_customer_data.py"):
            with self.subTest(file=f):
                self.assertNotIn(f, got)


class ItRunsOnThisRepositoryTest(unittest.TestCase):

    def test_the_current_contents_pass(self) -> None:
        """**Public に置いてよい状態であること。**

        手元に登録表（~/.config/mirumeai/customer_registry.txt など）があれば、
        それとも照合する。 通常の CI には登録表を渡さないので、 名前以外を
        照らす（名前は、 手元の登録表か、 main の検査だけが登録表を読む
        仕組みが照らす）。
        """
        r = subprocess.run([sys.executable,
                            str(HERE / "customer_data_check.py")],
                           capture_output=True, text=True,
                           cwd=str(HERE.parents[1]))
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-600:])

    def test_the_workflow_calls_it(self) -> None:
        """**再利用 workflow には入れないこと。**

        顧客 Repository の `config/` には PLC の IP が正当に入る。 そこで
        走らせると毎回 NG になり、 誰も見なくなる。
        """
        own = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("customer_data_check.py", own,
                      "本 Repository の workflow が呼んでいません")
        reusable = (HERE.parents[1] / ".github" / "workflows"
                    / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        self.assertNotIn("customer_data_check.py", reusable,
                         "再利用 workflow に入れると顧客 Repository で"
                         "毎回 NG になります")

    def test_no_workflow_gets_secrets_or_privileged_events(self) -> None:
        """**PR の code が動く workflow に、 登録表（secret）を渡さない。**

        書き込み権限があれば、 workflow や試験を書き換えて secret を読み出せる。
        この Repository の workflow は secret を一切使わない。 特権のイベント
        （pull_request_target・workflow_run）も、 信頼の境界を決めて試験ごと
        足すまでは使わない。
        """
        for wf in sorted((HERE.parents[1] / ".github" / "workflows").glob("*.y*ml")):
            text = wf.read_text(encoding="utf-8")
            with self.subTest(workflow=wf.name):
                self.assertNotIn(C.REGISTRY_ENV, text)
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

    def test_a_registry_left_in_the_repository_is_ignored(self) -> None:
        """誤ってリポジトリへ置いた登録表を commit しない（.gitignore）。 正式な
        置き場所はリポジトリの外。 追跡しているファイルまで無視しないこと。"""
        root = str(HERE.parents[1])
        for rel in ("customer_registry.txt", "sub/dir/customer_registry.txt",
                    "customer_registry.txt.bak"):
            with self.subTest(path=rel):
                r = subprocess.run(["git", "check-ignore", "-q", "--", rel], cwd=root)
                self.assertEqual(r.returncode, 0, f"{rel} を無視していません")
        ignored = subprocess.run(["git", "ls-files", "-ci", "--exclude-standard"],
                                 cwd=root, capture_output=True, text=True, check=True)
        self.assertEqual(ignored.stdout, "", "追跡しているファイルまで無視しています")

    def test_no_registry_is_kept_in_this_repository(self) -> None:
        """**公開のこのリポジトリに、 顧客の名前の登録表を置かない。**"""
        own = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--allow-registry-in-repo", own)
        files = subprocess.run(["git", "ls-files"], cwd=str(HERE.parents[1]),
                               capture_output=True, text=True, check=True).stdout
        self.assertNotIn("customer_registry", files)


if __name__ == "__main__":
    unittest.main()

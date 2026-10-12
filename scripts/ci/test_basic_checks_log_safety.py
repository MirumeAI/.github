#!/usr/bin/env python3
"""機械チェック（`basic_checks.py`）が、 **CI の記録に値と道を出さない**ことを確かめる（#50）。

顧客案件の Repository では、 設定のファイルの道や中身に、 その案件に固有の
名前（面・トリガ・機器）や値が入る。 誤りを見つけたときに、 PyYAML の誤りの文
（該当行の抜き出しを含む）・`bash -n` の出力・ファイルの道・`VERSION` の中身を
そのまま出していたので、 それが CI の記録に残った。 名前に改行と `::warning::`
を入れたファイルでは、 記録の上で Actions の命令として読まれる行も出た。

ここでは **架空の値だけ**を使う（IP は文書用の網）。 場面ごとに「安全か」を
判定し、 最後に検査の守りを 1 つずつ壊した版で同じ場面を流して、 判定が
落ちることも確かめる（壊しても通る試験は、 何も確かめていない）。

出力は **バイトで受けて、 改行を変えずに読む**（`text=True` は復帰を改行に
変えるので、 生の復帰が出ても試験で見えない）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "basic_checks.py"

sys.path.insert(0, str(HERE))
try:
    import basic_checks as BC     # 決まった名前と拡張子の一覧だけを使う
finally:
    sys.path.pop(0)

#: 顧客案件の Repository に見える origin（架空）と、 道をそのまま出す共通の
#: Repository（actions/checkout が付ける形は `.git` なし）。
CUSTOMER = "https://github.com/MirumeAI/customer-synthetic-example.git"
OPEN = "https://github.com/MirumeAI/PDM"

#: 架空の値。 **どれも記録に出てはいけない。**
SYN = {
    "ip": "192.0.2.77",
    "serial": "SN-SYNTH-0042",
    "serial2": "SNSYNTH7731",
    "trigger": "trig_synth_alpha",
    "face": "face-synth-alpha",
    "equipment": "eq-synth-0007",
    "plc": "DM9907",
    "company": "contosynth",
    "alias": "synth_alias_face",
    "tag": "synth_tag_trig",
}
FACE_DIR = f"config/pdm/{SYN['face']}/bbox_acquire"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False

IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _env(extra: "dict | None" = None, root: "Path | None" = None) -> dict:
    e = {k: v for k, v in os.environ.items()
         if k not in ("GITHUB_ACTIONS", "CI", "PYTHONWARNINGS")}
    if root is not None:
        e["CI_ROOT"] = str(root)
    e.update(extra or {})
    return e


def _out(r: subprocess.CompletedProcess) -> str:
    """バイトのまま受けた出力を、 改行を変えずに文字にする。"""
    return (r.stdout + r.stderr).decode("utf-8", errors="replace")


def run(files: dict, origin: "str | None" = CUSTOMER, args: tuple = (),
        env: "dict | None" = None, checker: Path = CHECKER,
        more_origins: tuple = ()) -> tuple:
    """一時の Git Repository を作って検査を流す。 (終了コード, 出力)。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        if origin:
            subprocess.run(["git", "remote", "add", "origin", origin],
                           cwd=root, check=True)
        for url in more_origins:
            subprocess.run(["git", "config", "--add", "remote.origin.url", url],
                           cwd=root, check=True)
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, bytes):
                p.write_bytes(body)
            else:
                p.write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
        r = subprocess.run([sys.executable, str(checker), *args],
                           capture_output=True, env=_env(env, root))
        return r.returncode, _out(r)


def leaked(out: str) -> list:
    """記録に出た架空の値の**種類**（値は返さない）。"""
    return [k for k, v in SYN.items() if v in out]


def command_lines(out: str) -> int:
    """Actions の命令として読まれうる行の数（行頭の `::` と、 どこでも読まれる `##[`）。

    `splitlines()` は復帰・U+0085・U+2028・U+2029 でも行を分ける。
    """
    return sum(1 for ln in out.splitlines()
               if ln.lstrip().startswith("::") or "##[" in ln)


def raw_controls(out: str) -> int:
    """改行の外の制御文字・書式の文字・行と段落の区切りの数。"""
    return sum(1 for ch in out if ch != "\n"
               and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"))


def safe(code: int, out: str, want: int = 1) -> bool:
    return (code == want and not leaked(out) and "Traceback" not in out
            and command_lines(out) == 0 and raw_controls(out) == 0)


def ids_are_structural(out: str) -> bool:
    """伏せた道のどの部分も、 決まった名前か `<redacted#n>`（＋一覧の拡張子）だけか。"""
    for ln in out.split("\n"):
        if not ln.startswith("  ") or ln.startswith("  道は"):
            continue
        ident = ln.strip().split(":")[0]
        for part in ident.split("/"):
            if part in BC.GENERIC_PARTS:
                continue
            m = re.fullmatch(r"<redacted#\d+>(\.[a-z0-9]+)?", part)
            if not m or (m.group(1) and m.group(1) not in BC.SAFE_SUFFIXES):
                return False
    return True


def safe_redacted(code: int, out: str) -> bool:
    return safe(code, out) and ids_are_structural(out)


# ---- 場面（検査の版を差し替えられるように、 checker を受け取る） ----

BAD_YAML = (f"plc:\n  host: {SYN['ip']}\n  addr: [{SYN['plc']}\n"
            f"  trig: {SYN['trigger']}: x: y\n")


def sc_yaml_value(checker=CHECKER):
    """A: 壊れた YAML の行に値がある（誤りの文の抜き出しに出る）。"""
    return run({f"{FACE_DIR}/params.yaml": BAD_YAML}, checker=checker)


def sc_yaml_alias_tag(checker=CHECKER):
    """A: 別名とタグ。 ComposerError・ConstructorError の文には名前がそのまま入る。"""
    return run({"configs/alias.yaml": f"a: *{SYN['alias']}\n",
                "configs/tag.yaml": f"b: !{SYN['tag']} x\n"},
               origin=OPEN, checker=checker)


def sc_json_value(checker=CHECKER):
    return run({f"{FACE_DIR}/params.json": f'{{"host": "{SYN["ip"]}", }}\n'},
               checker=checker)


def sc_python_line(checker=CHECKER):
    return run({f"plugins/{SYN['face']}.py": f'TRIGGER = "{SYN["trigger"]}" "\n'},
               checker=checker)


def sc_python_warning(checker=CHECKER):
    """無効な escape の警告（3.12 からは既定で出る）。 中身は入力の文字を含む。"""
    return run({f"plugins/{SYN['face']}.py": 'X = "\\q"\n'},
               env={"PYTHONWARNINGS": "always"}, checker=checker)


def sc_path_names(checker=CHECKER):
    """B: 道の中に面・トリガ・機器・製造番号の形の名前がある。"""
    return run({
        f"{FACE_DIR}/{SYN['trigger']}_{SYN['equipment']}.json": '{"a": 1,}\n',
        f"{FACE_DIR}/{SYN['plc']}.json": "",
        f"docs/{SYN['serial']}.md": "",
    }, checker=checker)


def sc_suffix(checker=CHECKER):
    """B: 名前の最後の部分（拡張子に見える所）に値がある。"""
    return run({
        f"config/eqp.{SYN['plc']}": "",
        f"config/cam-{SYN['ip']}": "",
        f"config/license.{SYN['company']}": "",
        f"docs/note.{SYN['serial2']}": "",
    }, checker=checker)


def sc_lazy_numbers(checker=CHECKER):
    """誤りの無いファイルに番号を使わない（番号から数と並びが分かる）。"""
    return run({f"{FACE_DIR}/ok1.json": "{}\n", f"{FACE_DIR}/ok2.json": "{}\n",
                f"{FACE_DIR}/zz.json": "{bad\n"}, checker=checker)


def sc_shell(checker=CHECKER):
    return run({f"scripts/{SYN['face']}.sh": f"if [ x ]; then echo {SYN['ip']}\n"},
               checker=checker)


def sc_shell_name_number(checker=CHECKER):
    """名前に `: line N: ` があっても、 その数を行の番号として取らない。"""
    return run({"scripts/x: line 4411: y.sh": "if [ x ]; then echo 1\n"},
               checker=checker)


def sc_version(checker=CHECKER):
    return run({"VERSION": f"{SYN['serial']}\n"}, checker=checker)


def sc_nul_python(checker=CHECKER):
    """C: NUL を含む `.py`（3.10 では ValueError。 前は traceback が出た）。"""
    return run({f"plugins/{SYN['face']}.py": b"x = 1\x00\n"}, checker=checker)


def sc_not_git(checker=CHECKER):
    """C: CI_ROOT が Git でない（git ls-files が落ちる）。 道に架空の名前を入れる。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / SYN["face"]
        root.mkdir()
        r = subprocess.run([sys.executable, str(checker)],
                           capture_output=True, env=_env(None, root))
        return r.returncode, _out(r)


def sc_injection_open(checker=CHECKER):
    """命令の注入。 道をそのまま出す Repository でも、 命令として読まれないこと。"""
    return run({"cfg/\n::warning::INJECTED.json": "{bad\n",
                "cfg/##[add-mask]x.json": "{bad\n",
                "cfg/\u202ertl.json": "{bad\n"}, origin=OPEN, checker=checker)


def sc_root_command(checker=CHECKER):
    """根に置いた `::warning ...` の名前（改行が無いので `::` の置き換えだけが守り）。"""
    return run({"::warning title=x::A.json": "{bad\n"}, origin=OPEN,
               checker=checker)


def sc_controls(checker=CHECKER):
    """復帰・ESC・U+0085・U+2028・U+2029 を含む名前。"""
    return run({"c1\r::warning::R.json": "{bad\n",
                "c2\x1b[31mE.json": "{bad\n",
                "c3\x85::warning::N.json": "{bad\n",
                "c4\u2028::warning::L.json": "{bad\n",
                "c5\u2029::warning::P.json": "{bad\n"}, origin=OPEN,
               checker=checker)


def sc_non_utf8_name(checker=CHECKER):
    """UTF-8 でない名前。 止まらずに検査し、 その文字を無害な形で出す。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        subprocess.run(["git", "remote", "add", "origin", OPEN], cwd=root, check=True)
        with open(os.path.join(os.fsencode(root), b"bad\xff.json"), "wb") as f:
            f.write(b"{bad\n")
        subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
        r = subprocess.run([sys.executable, str(checker)],
                           capture_output=True, env=_env(None, root))
        return r.returncode, _out(r)


def sc_no_origin(checker=CHECKER):
    """どの Repository か分からないときは伏せる。"""
    return run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"}, origin=None,
               checker=checker)


def sc_profile_on_open_origin(checker=CHECKER):
    """宣言（customer-profile.yaml）があれば、 origin が共通の名前でも伏せる。"""
    return run({"customer-profile.yaml": "schema_version: 1\n",
                f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"}, origin=OPEN,
               checker=checker)


def sc_lookalike_origin(checker=CHECKER):
    """別のホストの道に `github.com/<共通の Repository>` を含むだけの origin。"""
    return run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"},
               origin="https://evil.example/github.com/MirumeAI/PDM", checker=checker)


def sc_two_origins(checker=CHECKER):
    """origin の URL が 2 つ（1 つ目が共通の Repository）。 分からないものとして伏せる。"""
    return run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"}, origin=OPEN,
               more_origins=(CUSTOMER,), checker=checker)


def sc_show_paths_in_ci(checker=CHECKER):
    return run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"},
               args=("--show-paths",), env={"GITHUB_ACTIONS": "true"},
               checker=checker)


def sc_unreadable(checker=CHECKER):
    """読めないファイルがあっても、 種類だけを出して残りを見る。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        # `.md` は秘密の走査だけが読む（`.json` だと JSON の検査の中で例外を受ける）
        p = root / FACE_DIR / f"{SYN['trigger']}.md"
        p.parent.mkdir(parents=True)
        p.write_text("ok\n", encoding="utf-8")
        (root / "other.json").write_text("{bad\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        subprocess.run(["git", "remote", "add", "origin", CUSTOMER], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        p.chmod(0)
        try:
            r = subprocess.run([sys.executable, str(checker)],
                               capture_output=True, env=_env(None, root))
        finally:
            p.chmod(0o644)
        return r.returncode, _out(r)


class ContentIsNotPrintedTest(unittest.TestCase):
    """**誤りの文に、 入力の行とパーサの文脈を出さない。** 種類と位置だけ。"""

    @unittest.skipUnless(HAVE_YAML, "PyYAML が無い")
    def test_a_broken_yaml_line_is_not_printed(self) -> None:
        code, out = sc_yaml_value()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
        self.assertRegex(out, r":\d+: YAML として読めない（[A-Za-z]+Error・列 \d+）")

    @unittest.skipUnless(HAVE_YAML, "PyYAML が無い")
    def test_alias_and_tag_names_are_not_printed(self) -> None:
        code, out = sc_yaml_alias_tag()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("configs/alias.yaml:1: YAML として読めない（ComposerError", out)
        self.assertIn("configs/tag.yaml:1: YAML として読めない（ConstructorError", out)

    @unittest.skipUnless(HAVE_YAML, "PyYAML が無い")
    def test_the_same_holds_where_paths_are_open(self) -> None:
        code, out = run({"configs/params.yaml": BAD_YAML}, origin=OPEN)
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("configs/params.yaml:", out)   # 道は既存のとおり出る

    def test_json_python_shell_show_only_the_position(self) -> None:
        for name, sc, pat in (
                ("json", sc_json_value, r":1: JSON として読めない（列 \d+）"),
                ("python", sc_python_line, r":1: 構文エラー（SyntaxError"),
                ("shell", sc_shell, r":2: シェルの構文エラー")):
            with self.subTest(kind=name):
                code, out = sc()
                self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
                self.assertRegex(out, pat)

    def test_python_warnings_are_not_printed(self) -> None:
        code, out = sc_python_warning()
        self.assertEqual(code, 0, out)
        self.assertNotIn("invalid escape", out)

    def test_a_number_in_a_name_is_not_taken_as_the_line(self) -> None:
        code, out = sc_shell_name_number()
        self.assertTrue(safe_redacted(code, out), out)
        self.assertNotIn("4411", out)
        self.assertRegex(out, r":2: シェルの構文エラー")

    def test_version_content_is_not_printed(self) -> None:
        code, out = sc_version()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("いまは 13 文字", out)


class PathsAreRedactedTest(unittest.TestCase):
    """**顧客案件の Repository と、 分からない Repository では道を伏せる。**"""

    def test_names_in_paths_are_redacted(self) -> None:
        code, out = sc_path_names()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
        # 決まった名前は残り、 案件に固有の部分は番号になる
        self.assertIn("config/pdm/<redacted#1>/bbox_acquire/<redacted#2>.json", out)
        self.assertIn("docs/<redacted#", out)

    def test_values_shaped_like_suffixes_are_dropped(self) -> None:
        code, out = sc_suffix()
        self.assertTrue(safe_redacted(code, out), out)
        for n in range(1, 5):
            self.assertRegex(out, rf"(config|docs)/<redacted#{n}>: 0 バイト")

    def test_the_same_name_gets_the_same_number(self) -> None:
        code, out = sc_path_names()
        dirs = {ln.split("/bbox_acquire/")[0].strip()
                for ln in out.splitlines() if "/bbox_acquire/" in ln}
        self.assertEqual(dirs, {"config/pdm/<redacted#1>"}, out)

    def test_files_without_errors_take_no_number(self) -> None:
        code, out = sc_lazy_numbers()
        self.assertTrue(safe_redacted(code, out), out)
        self.assertIn("config/pdm/<redacted#1>/bbox_acquire/<redacted#2>.json:1:", out)
        self.assertNotIn("<redacted#3>", out)

    def test_an_unknown_repository_is_redacted(self) -> None:
        code, out = sc_no_origin()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
        self.assertIn("<redacted#", out)

    def test_a_declaration_forces_redaction(self) -> None:
        code, out = sc_profile_on_open_origin()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))

    def test_lookalike_and_multiple_origins_are_redacted(self) -> None:
        for name, sc in (("別のホスト", sc_lookalike_origin), ("URL が 2 つ", sc_two_origins)):
            with self.subTest(origin=name):
                code, out = sc()
                self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
                self.assertIn("<redacted#", out)
        code, out = run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"},
                        origin="/srv/mirror/github.com/MirumeAI/PDM")
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))

    def test_open_repositories_keep_paths(self) -> None:
        """共通の Repository では、 既存のとおり道をそのまま出す（CI の形・https・ssh）。"""
        for origin in (OPEN, "https://github.com/MirumeAI/PDM.git",
                       "git@github.com:MirumeAI/Image-Acquisition-PDM.git"):
            with self.subTest(origin=origin):
                code, out = run({"tools/broken.json": "{bad\n"}, origin=origin)
                self.assertEqual(code, 1)
                self.assertIn("tools/broken.json:1: JSON として読めない", out)
                self.assertNotIn("<redacted#", out)

    def test_no_customer_repository_is_open(self) -> None:
        for name in BC.OPEN_PATH_REPOS:
            self.assertFalse(name.split("/", 1)[1].startswith("customer-"), name)

    def test_show_paths_is_refused_in_ci(self) -> None:
        code, out = sc_show_paths_in_ci()
        self.assertTrue(safe(code, out, want=2), (code, leaked(out)))
        self.assertIn("CI では使えません", out)

    def test_show_paths_works_by_hand(self) -> None:
        code, out = run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"},
                        args=("--show-paths",))
        self.assertEqual(code, 1)
        self.assertIn(SYN["trigger"], out)    # 手元では元の道を見られる


class NoTracebackTest(unittest.TestCase):
    """**想定外の例外でも、 traceback と元の文を出さない。**"""

    def test_nul_in_python(self) -> None:
        code, out = sc_nul_python()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
        self.assertIn("構文エラー（ValueError", out)

    def test_not_a_git_repository(self) -> None:
        code, out = sc_not_git()
        self.assertTrue(safe(code, out, want=2), (code, leaked(out)))
        self.assertIn("ERROR: 検査の途中で想定外の例外（CalledProcessError）", out)

    @unittest.skipIf(IS_ROOT, "root では読めないファイルを作れない")
    def test_an_unreadable_file(self) -> None:
        code, out = sc_unreadable()
        self.assertTrue(safe_redacted(code, out), (code, leaked(out)))
        self.assertIn("検査できない（PermissionError）", out)
        # 残りのファイルも見る（顧客案件の Repository なので、 名前は伏せて出る）
        self.assertRegex(out, r"<redacted#\d+>\.json:1: JSON として読めない")


class NoWorkflowCommandTest(unittest.TestCase):
    """**名前に改行・`::`・`##[`・制御文字・書式の文字があっても、 命令として読まれない。**"""

    def test_injection_where_paths_are_open(self) -> None:
        code, out = sc_injection_open()
        self.assertTrue(safe(code, out), (code, command_lines(out), raw_controls(out)))
        self.assertIn("\\u000a", out)          # 改行は見える形で残る
        self.assertIn("\\u202e", out)

    def test_a_command_at_the_root(self) -> None:
        code, out = sc_root_command()
        self.assertTrue(safe(code, out), (code, command_lines(out)))

    def test_carriage_return_esc_and_separators(self) -> None:
        code, out = sc_controls()
        self.assertTrue(safe(code, out), (code, command_lines(out), raw_controls(out)))
        self.assertIn("NG: 5 件", out)       # 復帰を含む名前も検査から漏れない
        for esc in ("\\u000d", "\\u001b", "\\u0085", "\\u2028", "\\u2029"):
            self.assertIn(esc, out)

    def test_a_name_that_is_not_utf8(self) -> None:
        code, out = sc_non_utf8_name()
        self.assertTrue(_non_utf8(code, out), out)

    def test_injection_where_paths_are_redacted(self) -> None:
        code, out = run({"cfg/\n::warning::INJECTED.json": "{bad\n",
                         "cfg/##[add-mask]x.json": "{bad\n"})
        self.assertTrue(safe_redacted(code, out), (code, command_lines(out)))
        self.assertNotIn("INJECTED", out)


class RegressionTest(unittest.TestCase):

    def test_a_clean_repository_passes(self) -> None:
        for origin in (CUSTOMER, OPEN, None):
            with self.subTest(origin=origin):
                code, out = run({"README.md": "ok\n", "config/config.json": "{}\n"},
                                origin=origin)
                self.assertEqual(code, 0, out)
                self.assertIn("OK: 問題なし", out)
                self.assertNotIn("<redacted#", out)


# ---- 守りを 1 つずつ壊した版 ----

def _safe1(code, out):
    return safe(code, out)


def _safe2(code, out):
    return safe(code, out, want=2)


def _unreadable(code, out):
    return safe_redacted(code, out) and "検査できない（PermissionError）" in out


def _warning(code, out):
    return code == 0 and "invalid escape" not in out


def _shell_number(code, out):
    return safe_redacted(code, out) and "4411" not in out


def _lazy(code, out):
    nums = [int(n) for n in re.findall(r"<redacted#(\d+)>", out)]
    return (safe_redacted(code, out) and max(nums, default=0) == 2
            and "config/pdm/<redacted#1>/bbox_acquire/<redacted#2>.json:1:" in out)


def _controls(code, out):
    return safe(code, out) and "NG: 5 件" in out


def _non_utf8(code, out):
    return safe(code, out) and "bad\\udcff.json:1: JSON として読めない" in out


#: (名前, 壊す前, 壊した後, その守りを見る場面, 本物の版で True になる判定)
MUTANTS = [
    ("道を伏せない",
     "        if self.open_paths:\n",
     "        if True:\n", sc_path_names, safe_redacted),
    ("拡張子を一覧で絞らない",
     "            if suffix not in SAFE_SUFFIXES:\n",
     "            if False:\n", sc_suffix, safe_redacted),
    ("番号をすべてのファイルに振る",
     "        rid = LazyId(rid_of, rel)\n",
     "        rid = rid_of(rel)\n", sc_lazy_numbers, _lazy),
    ("YAML の誤りの文を出す",
     'errors.append(f"{rid}{line}: YAML として読めない（{type(e).__name__}{col}）")',
     'errors.append(f"{rid}{line}: YAML として読めない: {e}")', sc_yaml_value, safe_redacted),
    ("YAML の problem を出す",
     'errors.append(f"{rid}{line}: YAML として読めない（{type(e).__name__}{col}）")',
     "errors.append(f\"{rid}{line}: YAML として読めない（{type(e).__name__}{col}・{getattr(e, 'problem', '')}）\")",
     sc_yaml_alias_tag, _safe1),
    ("JSON の入力を出す",
     'errors.append(f"{rid}:{e.lineno}: JSON として読めない（列 {e.colno}）")',
     'errors.append(f"{rid}:{e.lineno}: JSON として読めない: {e.doc}")', sc_json_value,
     safe_redacted),
    ("Python の元の行を出す",
     "errors.append(f\"{rid}:{e.lineno or '?'}: 構文エラー（{type(e).__name__}{col}）\")",
     'errors.append(f"{rid}:{e.lineno}: 構文エラー: {e.text}")', sc_python_line, safe_redacted),
    ("Python の警告を出す",
     '                warnings.simplefilter("ignore")\n',
     "                pass\n", sc_python_warning, _warning),
    ("bash の出力を出す",
     "errors.append(f\"{rid}{':' + m.group(1) if m else ''}: シェルの構文エラー\")",
     'errors.append(f"{rid}: シェルの構文エラー: {r.stderr}")', sc_shell, safe_redacted),
    ("名前の中の数を行の番号に取る",
     r'm = re.match(re.escape(str(p)) + r": line (\d+): ", r.stderr)',
     r'm = re.search(r": line (\d+): ", r.stderr)', sc_shell_name_number, _shell_number),
    ("VERSION の中身を出す",
     'f"（いまは {len(v)} 文字）"', 'f"（いまは {v[:40]!r}）"', sc_version, _safe1),
    ("命令を無害にしない",
     "def neutral(text: str) -> str:\n",
     "def neutral(text: str) -> str:\n    return text\n", sc_injection_open, _safe1),
    ("行頭の :: を崩さない",
     r'.replace("::", ":\\u003a")', "", sc_root_command, _safe1),
    ("行と段落の区切りを無害にしない",
     '("Cc", "Cf", "Zl", "Zp", "Cs")', '("Cc", "Cf", "Cs")', sc_controls, _controls),
    ("改行と書式の文字だけを無害にする",
     'if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp", "Cs"):',
     'if ch == "\\n" or unicodedata.category(ch) == "Cf":', sc_controls, _controls),
    ("UTF-8 でない名前の文字を無害にしない",
     '("Cc", "Cf", "Zl", "Zp", "Cs")', '("Cc", "Cf", "Zl", "Zp")', sc_non_utf8_name,
     _non_utf8),
    ("名前を改行を変える読み方で読む",
     'check=True).stdout.decode("utf-8", "surrogateescape")',
     'check=True, text=True).stdout', sc_controls, _controls),
    ("traceback を出す",
     "    except Exception as e:\n        # **traceback",
     "    except ZeroDivisionError as e:\n        # **traceback", sc_not_git, _safe2),
    ("分からないときに道を出す",
     "    return origin_repo(root) in OPEN_PATH_REPOS\n",
     "    return True\n", sc_no_origin, safe_redacted),
    ("origin をホストに固定しない",
     r"^(?:https://(?:[^/@\s]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)",
     r"github\.com[:/]+", sc_lookalike_origin, safe_redacted),
    ("origin が複数でも決める",
     "    if len(urls) != 1:\n        return None\n",
     "    if not urls:\n        return None\n", sc_two_origins, safe_redacted),
    ("宣言を見ない",
     '    if (root / "customer-profile.yaml").is_file():\n',
     "    if False:\n", sc_profile_on_open_origin, safe_redacted),
    ("CI で --show-paths を許す",
     '    if show_paths and (os.environ.get("GITHUB_ACTIONS") == "true"\n',
     '    if False and (os.environ.get("GITHUB_ACTIONS") == "true"\n', sc_show_paths_in_ci,
     _safe2),
]
if not IS_ROOT:
    MUTANTS.append(
        ("1 ファイルの例外で止まる",
         "        except Exception as e:\n            # **例外の文は",
         "        except ZeroDivisionError as e:\n            # **例外の文は",
         sc_unreadable, _unreadable))

_NEEDS_YAML = (sc_yaml_value, sc_yaml_alias_tag)


class EachGuardIsTestedTest(unittest.TestCase):
    """**守りを 1 つ壊すと、 その場面の判定が落ちること。**"""

    def test_the_real_checker_is_judged_safe(self) -> None:
        for name, _old, _new, sc, judge in MUTANTS:
            if sc in _NEEDS_YAML and not HAVE_YAML:
                continue
            with self.subTest(guard=name):
                self.assertTrue(judge(*sc()), name)

    def test_every_mutant_is_caught(self) -> None:
        src = CHECKER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            for name, old, new, sc, judge in MUTANTS:
                if sc in _NEEDS_YAML and not HAVE_YAML:
                    continue
                with self.subTest(mutant=name):
                    self.assertEqual(src.count(old), 1, f"壊す場所が 1 つでない: {name}")
                    m = Path(td) / "basic_checks.py"
                    m.write_text(src.replace(old, new), encoding="utf-8")
                    self.assertFalse(judge(*sc(checker=m)), f"守りを壊しても通った: {name}")


if __name__ == "__main__":
    unittest.main()

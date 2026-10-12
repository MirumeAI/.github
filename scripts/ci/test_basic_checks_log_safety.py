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
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKER = HERE / "basic_checks.py"

#: 顧客案件の Repository に見える origin（架空）と、 道をそのまま出す共通の Repository。
CUSTOMER = "https://github.com/MirumeAI/customer-synthetic-example.git"
OPEN = "https://github.com/MirumeAI/PDM.git"

#: 架空の値。 **どれも記録に出てはいけない。**
SYN = {
    "ip": "192.0.2.77",
    "serial": "SN-SYNTH-0042",
    "trigger": "trig_synth_alpha",
    "face": "face-synth-alpha",
    "equipment": "eq-synth-0007",
    "plc": "DM9907",
}
FACE_DIR = f"config/pdm/{SYN['face']}/bbox_acquire"

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def run(files: dict, origin: "str | None" = CUSTOMER, args: tuple = (),
        env: "dict | None" = None, checker: Path = CHECKER) -> tuple:
    """一時の Git Repository を作って検査を流す。 (終了コード, 出力)。"""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "."], cwd=root, check=True)
        if origin:
            subprocess.run(["git", "remote", "add", "origin", origin],
                           cwd=root, check=True)
        for rel, body in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(body, bytes):
                p.write_bytes(body)
            else:
                p.write_text(body, encoding="utf-8")
        subprocess.run(["git", "add", "-A", "-f"], cwd=root, check=True)
        e = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "CI")}
        e["CI_ROOT"] = str(root)
        e.update(env or {})
        r = subprocess.run([sys.executable, str(checker), *args],
                           capture_output=True, text=True, env=e)
        return r.returncode, r.stdout + r.stderr


def leaked(out: str) -> list:
    """記録に出た架空の値の**種類**（値は返さない）。"""
    return [k for k, v in SYN.items() if v in out]


def command_lines(out: str) -> int:
    """Actions の命令として読まれうる行の数（行頭の `::` と、 どこでも読まれる `##[`）。"""
    return sum(1 for ln in out.splitlines()
               if ln.lstrip().startswith("::") or "##[" in ln)


def raw_controls(out: str) -> int:
    """改行の外の制御文字・書式の文字の数。"""
    return sum(1 for ch in out if ch != "\n"
               and unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"))


def safe(code: int, out: str, want: int = 1) -> bool:
    return (code == want and not leaked(out) and "Traceback" not in out
            and command_lines(out) == 0 and raw_controls(out) == 0)


# ---- 場面（検査の版を差し替えられるように、 checker を受け取る） ----

BAD_YAML = (f"plc:\n  host: {SYN['ip']}\n  addr: [{SYN['plc']}\n"
            f"  trig: {SYN['trigger']}: x: y\n")


def sc_yaml_value(checker=CHECKER):
    """A: 壊れた YAML の行に値がある（誤りの文の抜き出しに出る）。"""
    return run({f"{FACE_DIR}/params.yaml": BAD_YAML}, checker=checker)


def sc_json_value(checker=CHECKER):
    return run({f"{FACE_DIR}/params.json": f'{{"host": "{SYN["ip"]}", }}\n'},
               checker=checker)


def sc_python_line(checker=CHECKER):
    return run({f"plugins/{SYN['face']}.py": f'TRIGGER = "{SYN["trigger"]}" "\n'},
               checker=checker)


def sc_path_names(checker=CHECKER):
    """B: 道の中に面・トリガ・機器・製造番号の形の名前がある。"""
    return run({
        f"{FACE_DIR}/{SYN['trigger']}_{SYN['equipment']}.json": '{"a": 1,}\n',
        f"{FACE_DIR}/{SYN['plc']}.json": "",
        f"docs/{SYN['serial']}.md": "",
    }, checker=checker)


def sc_shell(checker=CHECKER):
    return run({f"scripts/{SYN['face']}.sh": f"if [ x ]; then echo {SYN['ip']}\n"},
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
        e = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "CI")}
        e["CI_ROOT"] = str(root)
        r = subprocess.run([sys.executable, str(checker)],
                           capture_output=True, text=True, env=e)
        return r.returncode, r.stdout + r.stderr


def sc_injection_open(checker=CHECKER):
    """命令の注入。 道をそのまま出す Repository でも、 命令として読まれないこと。"""
    return run({"cfg/\n::warning::INJECTED.json": "{bad\n",
                "cfg/##[add-mask]x.json": "{bad\n",
                "cfg/\u202ertl.json": "{bad\n"}, origin=OPEN, checker=checker)


def sc_no_origin(checker=CHECKER):
    """どの Repository か分からないときは伏せる。"""
    return run({f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"}, origin=None,
               checker=checker)


def sc_profile_on_open_origin(checker=CHECKER):
    """宣言（customer-profile.yaml）があれば、 origin が共通の名前でも伏せる。"""
    return run({"customer-profile.yaml": "schema_version: 1\n",
                f"{FACE_DIR}/{SYN['trigger']}.json": "{bad\n"}, origin=OPEN,
               checker=checker)


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
            e = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "CI")}
            e["CI_ROOT"] = str(root)
            r = subprocess.run([sys.executable, str(checker)],
                               capture_output=True, text=True, env=e)
        finally:
            p.chmod(0o644)
        return r.returncode, r.stdout + r.stderr


IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


class ContentIsNotPrintedTest(unittest.TestCase):
    """**誤りの文に、 入力の行とパーサの文脈を出さない。** 種類と位置だけ。"""

    @unittest.skipUnless(HAVE_YAML, "PyYAML が無い")
    def test_a_broken_yaml_line_is_not_printed(self) -> None:
        code, out = sc_yaml_value()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("YAML として読めない", out)
        self.assertRegex(out, r":\d+: YAML として読めない（[A-Za-z]+Error・列 \d+）")

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
                self.assertTrue(safe(code, out), (code, leaked(out)))
                self.assertRegex(out, pat)

    def test_version_content_is_not_printed(self) -> None:
        code, out = sc_version()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("いまは 13 文字", out)


class PathsAreRedactedTest(unittest.TestCase):
    """**顧客案件の Repository と、 分からない Repository では道を伏せる。**"""

    def test_names_in_paths_are_redacted(self) -> None:
        code, out = sc_path_names()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        # 決まった名前は残り、 案件に固有の部分は番号になる
        self.assertIn("config/pdm/<redacted#1>/bbox_acquire/<redacted#2>.json", out)
        self.assertIn("docs/<redacted#", out)

    def test_the_same_name_gets_the_same_number(self) -> None:
        code, out = sc_path_names()
        dirs = {ln.split("/bbox_acquire/")[0].strip()
                for ln in out.splitlines() if "/bbox_acquire/" in ln}
        self.assertEqual(dirs, {"config/pdm/<redacted#1>"}, out)

    def test_an_unknown_repository_is_redacted(self) -> None:
        code, out = sc_no_origin()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("<redacted#", out)

    def test_a_declaration_forces_redaction(self) -> None:
        code, out = sc_profile_on_open_origin()
        self.assertTrue(safe(code, out), (code, leaked(out)))

    def test_open_repositories_keep_paths(self) -> None:
        """共通の Repository では、 既存のとおり道をそのまま出す（https と ssh）。"""
        for origin in (OPEN, "git@github.com:MirumeAI/Image-Acquisition-PDM.git"):
            with self.subTest(origin=origin):
                code, out = run({"tools/broken.json": "{bad\n"}, origin=origin)
                self.assertEqual(code, 1)
                self.assertIn("tools/broken.json:1: JSON として読めない", out)
                self.assertNotIn("<redacted#", out)

    def test_no_customer_repository_is_open(self) -> None:
        sys.path.insert(0, str(HERE))
        try:
            import basic_checks
        finally:
            sys.path.pop(0)
        for name in basic_checks.OPEN_PATH_REPOS:
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
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("構文エラー（ValueError", out)

    def test_not_a_git_repository(self) -> None:
        code, out = sc_not_git()
        self.assertTrue(safe(code, out, want=2), (code, leaked(out)))
        self.assertIn("ERROR: 検査の途中で想定外の例外（CalledProcessError）", out)

    @unittest.skipIf(IS_ROOT, "root では読めないファイルを作れない")
    def test_an_unreadable_file(self) -> None:
        code, out = sc_unreadable()
        self.assertTrue(safe(code, out), (code, leaked(out)))
        self.assertIn("検査できない（PermissionError）", out)
        # 残りのファイルも見る（顧客案件の Repository なので、 名前は伏せて出る）
        self.assertRegex(out, r"<redacted#\d+>\.json:1: JSON として読めない")


class NoWorkflowCommandTest(unittest.TestCase):
    """**名前に改行・`::`・`##[`・書式の文字があっても、 命令として読まれない。**"""

    def test_injection_where_paths_are_open(self) -> None:
        code, out = sc_injection_open()
        self.assertTrue(safe(code, out), (code, command_lines(out), raw_controls(out)))
        self.assertIn("\\u000a", out)          # 改行は見える形で残る
        self.assertIn("\\u202e", out)

    def test_injection_where_paths_are_redacted(self) -> None:
        code, out = run({"cfg/\n::warning::INJECTED.json": "{bad\n",
                         "cfg/##[add-mask]x.json": "{bad\n"})
        self.assertTrue(safe(code, out), (code, command_lines(out)))
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


#: 守りを 1 つずつ壊した版。 (名前, 壊す前, 壊した後, その守りを見る場面)
MUTANTS = [
    ("道を伏せない",
     "        if self.open_paths:\n",
     "        if True:\n", sc_path_names),
    ("YAML の誤りの文を出す",
     'errors.append(f"{rid}{line}: YAML として読めない（{type(e).__name__}{col}）")',
     'errors.append(f"{rid}{line}: YAML として読めない: {e}")', sc_yaml_value),
    ("JSON の入力を出す",
     'errors.append(f"{rid}:{e.lineno}: JSON として読めない（列 {e.colno}）")',
     'errors.append(f"{rid}:{e.lineno}: JSON として読めない: {e.doc}")', sc_json_value),
    ("Python の元の行を出す",
     "errors.append(f\"{rid}:{e.lineno or '?'}: 構文エラー（{type(e).__name__}{col}）\")",
     'errors.append(f"{rid}:{e.lineno}: 構文エラー: {e.text}")', sc_python_line),
    ("bash の出力を出す",
     "errors.append(f\"{rid}{':' + m.group(1) if m else ''}: シェルの構文エラー\")",
     'errors.append(f"{rid}: シェルの構文エラー: {r.stderr}")', sc_shell),
    ("VERSION の中身を出す",
     'f"（いまは {len(v)} 文字）"', 'f"（いまは {v[:40]!r}）"', sc_version),
    ("命令を無害にしない",
     "def neutral(text: str) -> str:\n",
     "def neutral(text: str) -> str:\n    return text\n", sc_injection_open),
    ("traceback を出す",
     "    except Exception as e:\n        # **traceback",
     "    except ZeroDivisionError as e:\n        # **traceback", sc_not_git),
    ("分からないときに道を出す",
     "    return origin_repo(root) in OPEN_PATH_REPOS\n",
     "    return True\n", sc_no_origin),
    ("宣言を見ない",
     '    if (root / "customer-profile.yaml").is_file():\n',
     "    if False:\n", sc_profile_on_open_origin),
    ("CI で --show-paths を許す",
     '    if show_paths and (os.environ.get("GITHUB_ACTIONS") == "true"\n',
     '    if False and (os.environ.get("GITHUB_ACTIONS") == "true"\n', sc_show_paths_in_ci),
]
if not IS_ROOT:
    MUTANTS.append(
        ("1 ファイルの例外で止まる",
         "        except Exception as e:\n            # **例外の文は",
         "        except ZeroDivisionError as e:\n            # **例外の文は", sc_unreadable))


def _judge(name: str, code: int, out: str) -> bool:
    """場面ごとの「安全で、 期待どおり」の判定（本物の版で True になるもの）。"""
    if name in ("traceback を出す", "CI で --show-paths を許す"):
        return safe(code, out, want=2)
    if name == "1 ファイルの例外で止まる":
        return safe(code, out) and "検査できない（PermissionError）" in out
    return safe(code, out)


class EachGuardIsTestedTest(unittest.TestCase):
    """**守りを 1 つ壊すと、 その場面の判定が落ちること。**"""

    def test_the_real_checker_is_judged_safe(self) -> None:
        for name, _old, _new, sc in MUTANTS:
            if sc in (sc_yaml_value,) and not HAVE_YAML:
                continue
            with self.subTest(guard=name):
                self.assertTrue(_judge(name, *sc()), name)

    def test_every_mutant_is_caught(self) -> None:
        src = CHECKER.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as td:
            for name, old, new, sc in MUTANTS:
                if sc in (sc_yaml_value,) and not HAVE_YAML:
                    continue
                with self.subTest(mutant=name):
                    self.assertEqual(src.count(old), 1, f"壊す場所が 1 つでない: {name}")
                    m = Path(td) / "basic_checks.py"
                    m.write_text(src.replace(old, new), encoding="utf-8")
                    self.assertFalse(_judge(name, *sc(checker=m)),
                                     f"守りを壊しても通った: {name}")


if __name__ == "__main__":
    unittest.main()

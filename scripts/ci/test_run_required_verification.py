#!/usr/bin/env python3
"""`run_required_verification.py` が、 workflow と同じ段を流し、 PASS を偽らないことを確かめる。

**merge の前提が、 この道具の「PASS」になる。** 段を黙って飛ばす・落ちた段を
通す・知らない書き方を読み流すと、 確かめていない head が merge される。
手で組み立てていた頃は、 段の抜けと、 PyYAML が無いまま試験が飛ばされた
「OK」が実際に起きた。 見つける側が壊れていないことを、 小さな Repository で
実際に流して確かめる。
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNNER = HERE / "run_required_verification.py"
PYVER = "%d.%d" % sys.version_info[:2]

_spec = importlib.util.spec_from_file_location("run_required_verification", RUNNER)
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)


def _git(repo: Path, *args) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           *args], capture_output=True, text=True, check=True).stdout.strip()


def _write(r: Path, files: dict) -> None:
    for rel, text in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")


def _repo(root: Path, base: dict, change: dict, name: str = "target", main_after: dict | None = None) -> Path:
    """main に base、 枝 pr に change を commit した Repository（main_after は枝の後に main へ足す）。"""
    r = root / name
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _write(r, base)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "--allow-empty", "-m", "base")
    _git(r, "switch", "-q", "-c", "pr")
    _write(r, change)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "--allow-empty", "-m", "change")
    if main_after:
        _git(r, "switch", "-q", "main")
        _write(r, main_after)
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "main moved")
        _git(r, "switch", "-q", "pr")
    return r


def _state(repo: Path) -> tuple:
    return (_git(repo, "status", "--porcelain", "--ignored"), _git(repo, "for-each-ref"),
            _git(repo, "rev-parse", "HEAD"), _git(repo, "config", "--list", "--local"))


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/status").read_text().split("State:")[1].split()[0]
    except (OSError, IndexError):
        return False
    return state not in ("Z", "X")


@unittest.skipIf(R.yaml is None, "PyYAML が要る（CI では先の段で入れる）")
class E2E(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.runs = 0

    def tearDown(self):
        self._tmp.cleanup()

    def run_it(self, target: Path, *extra, canonical: Path | None = None, env: dict | None = None):
        self.runs += 1
        self.work = self.root / f"work-{self.runs}"
        cmd = [sys.executable, str(RUNNER), str(target), "--head", "pr", "--base", "main",
               "--canonical", str(canonical or target), "--canonical-ref", "main",
               "--python", f"{PYVER}={sys.executable}", "--work", str(self.work), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, **(env or {})))

    def wf(self, body: str, name: str = "w.yml") -> dict:
        return {f".github/workflows/{name}": body}

    def assertRefused(self, p, word: str):
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(word, p.stderr)
        self.assertNotIn("job", p.stdout)
        self.assertFalse(self.work.exists(), "止めるときは何も流さない")


class PassAndRecordTest(E2E):

    def test_pass_prints_record_with_counts_and_leaves_source_alone(self):
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              unit:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: actions/setup-python@v5
                    with: {{python-version: "{PYVER}"}}
                  - name: 試験
                    run: python -m unittest discover -s tests -v
            """), {"tests/test_a.py": """\
            import unittest
            class T(unittest.TestCase):
                def test_ok(self): pass
                @unittest.skip("見本")
                def test_skip(self): pass
            """})
        before = _state(t)
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(R.RECORD_PASS, p.stdout)
        self.assertIn("試験 2 件・飛ばした試験 1 件", p.stdout)
        self.assertIn(_git(t, "rev-parse", "pr")[:12], p.stdout)
        self.assertIn("を含む: はい", p.stdout)
        self.assertIn("正本と違う", p.stdout, "正本に無い道具で流したことを記録に出す")
        self.assertEqual(before, _state(t), "元の clone を変えてはいけない")

    def test_python_of_the_workflow_is_used_in_a_fresh_venv(self):
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - uses: actions/setup-python@v5
                    with: {{python-version: "{PYVER}"}}
                  - run: |
                      python -c "import sys; assert sys.prefix != sys.base_prefix, 'venv でない'"
                      test "$(python -c 'import sys; print(*sys.version_info[:2], sep=".")')" = "{PYVER}"
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_head_without_base_is_marked_in_the_record(self):
        t = _repo(self.root, {**self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), **self.wf("""\
            on: {pull_request: {paths: ["docs/**"]}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
            """, name="docs.yml")}, {"src/a.py": "x"}, main_after={"docs/new.md": "x"})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("を含む: いいえ", p.stdout)
        self.assertIn("main を取り込んで流し直して", p.stdout)
        # main の側の変更（docs/）は、 この PR の変更に数えない（merge-base からの差）
        self.assertIn("docs.yml（paths に合う変更が無い）", p.stdout)


class FailureIsNeverPassTest(E2E):

    def test_failed_step_fails_and_only_always_steps_continue(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                  - name: 落ちる
                    run: exit 3
                  - name: 流れてはいけない
                    run: echo no
                  - name: いつも流す
                    if: always()
                    run: echo always-ran
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertNotIn(R.RECORD_PASS, p.stdout)
        self.assertIn("結果: FAIL", p.stdout)
        self.assertRegex(p.stdout, r"03 skipped\s+流れてはいけない")
        self.assertRegex(p.stdout, r"04 ok\s+いつも流す")

    def test_needed_job_failure_stops_dependent_job(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              b:
                runs-on: ubuntu-latest
                needs: a
                steps:
                  - run: echo b
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("前の job が落ちたので流さない", p.stdout)

    def test_job_timeout_is_enforced(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                timeout-minutes: 0.05
                steps:
                  - run: sleep 60
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"01 timeout")

    def test_continue_on_error_github_path_and_step_timeout(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
                    continue-on-error: true
                  - run: |
                      mkdir -p "$RUNNER_TEMP/bin"
                      printf '#!/bin/sh\\necho from-path\\n' > "$RUNNER_TEMP/bin/my-tool"
                      chmod +x "$RUNNER_TEMP/bin/my-tool"
                      echo "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"
                  - run: my-tool
                  - run: sleep 60
                    timeout-minutes: 0.05
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"01 failed（続けた）")
        self.assertRegex(p.stdout, r"03 ok")
        self.assertRegex(p.stdout, r"04 timeout")

    def test_background_process_is_stopped_after_the_step(self):
        pid_file = self.root / "pid"
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: sleep 300 & echo $! > "{pid_file}"
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        pid = int(pid_file.read_text())
        for _ in range(50):
            if not _alive(pid):
                break
            time.sleep(0.1)
        self.assertFalse(_alive(pid), "段が終わったら、 後ろで動く子も止める")


class RefusesWhatItCannotRunTest(E2E):
    """**知らない書き方は、 何も流す前に止める**（終了コード 2）。"""

    def _refused(self, body: str, word: str):
        t = _repo(self.root, self.wf(body), {})
        self.assertRefused(self.run_it(t), word)

    def test_unknown_action(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/cache@v4
            """, "actions/cache")

    def test_matrix(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                strategy: {matrix: {v: [1, 2]}}
                steps:
                  - run: echo
            """, "strategy")

    def test_defaults(self):
        self._refused("""\
            on: pull_request
            defaults: {run: {working-directory: sub}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo
            """, "defaults")

    def test_unknown_expression(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "${{ secrets.TOKEN }}"
            """, "secrets.TOKEN")

    def test_head_ref_is_not_guessed(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                if: github.head_ref != ''
                steps:
                  - run: exit 1
            """, "github.head_ref")

    def test_pull_request_target(self):
        self._refused("""\
            on: [pull_request_target]
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo
            """, "pull_request_target")

    def test_filter_must_be_a_list(self):
        self._refused("""\
            on: {pull_request: {branches: main}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo
            """, "branches は文字の並び")

    def test_paths_outside_the_workspace(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/checkout@v4
                    with: {path: /tmp/outside}
            """, "作業場所の外")
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo
                    working-directory: ../..
            """), {}, name="target2")
        self.assertRefused(self.run_it(t), "作業場所の外")

    def test_missing_python_version(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - uses: actions/setup-python@v5
                    with: {python-version: "9.9"}
            """), {})
        p = subprocess.run([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main",
                            "--canonical", str(t), "--canonical-ref", "main"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("Python 9.9 が見つかりません", p.stderr)

    def test_no_pull_request_workflow_is_not_pass(self):
        t = _repo(self.root, self.wf("""\
            on: {push: {tags: ["v*"]}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("PASS とは言えません", p.stderr)


class FiltersTest(E2E):

    def _selected(self, on: str, change: dict, name: str, env: dict | None = None) -> int:
        t = _repo(self.root, self.wf(f"""\
            on: {on}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """), change, name=name)
        return self.run_it(t, env=env).returncode

    def test_filters_use_the_changed_files_and_the_base_branch(self):
        self.assertEqual(self._selected('{pull_request: {paths: ["docs/**", "!docs/skip.md"]}}',
                                        {"docs/skip.md": "x"}, "t1"), 2)
        self.assertEqual(self._selected('{pull_request: {paths: ["docs/**", "!docs/skip.md"]}}',
                                        {"docs/a/b.md": "x"}, "t2"), 0)
        self.assertEqual(self._selected('{pull_request: {paths-ignore: ["docs/**"]}}',
                                        {"docs/a.md": "x"}, "t3"), 2)
        self.assertEqual(self._selected('{pull_request: {paths-ignore: ["docs/**"]}}',
                                        {"docs/a.md": "x", "src/a.py": "x"}, "t4"), 0)
        self.assertEqual(self._selected('{pull_request: {branches: ["main"]}}', {}, "t5"), 0)
        self.assertEqual(self._selected('{pull_request: {branches: ["develop"]}}', {}, "t6"), 2)
        self.assertEqual(self._selected('{pull_request: {types: ["closed"]}}', {}, "t7"), 2)

    def test_non_ascii_names_match_the_filters(self):
        """ASCII でない名前のファイルの変更も paths に合い、 その名前の workflow も読む（#73）。

        git の既定（core.quotepath）では `"docs/\\343..."` の形で出て、 `docs/**` に合わず、
        その workflow が黙って「対象外」になっていた。 workflow の名前も同じ形で出て、
        `.yml` で終わらず読まれなかった。 **git の既定（引用する）を固定して流す**（流す人の設定で
        `core.quotepath=false` になっていると、 直す前の道具でも通ってしまう）。
        """
        quote = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.quotepath", "GIT_CONFIG_VALUE_0": "true"}
        self.assertEqual(self._selected('{pull_request: {paths: ["docs/**"]}}',
                                        {"docs/手順書/setup.sh": "x"}, "t8", env=quote), 0)
        self.assertEqual(self._selected('{pull_request: {paths: ["docs/手順書/**"]}}',
                                        {"docs/手順書/setup.sh": "x"}, "t9", env=quote), 0)
        self.assertEqual(self._selected('{pull_request: {paths-ignore: ["docs/手順書/**"]}}',
                                        {"docs/手順書/setup.sh": "x"}, "t10", env=quote), 2)
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """, name="検査.yml"), {"a.txt": "x"}, name="t11")
        p = self.run_it(t, "--list", env=quote)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("検査.yml", p.stdout)

    def test_workflow_names_that_break_the_output_are_refused(self):
        """制御文字を含む名前の workflow は、 流す前に止める（#73。 出力に記録の行に似た行を作れる）。"""
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """, name="a\nGitHub Actions unavailable.yml"), {"a.txt": "x"}, name="t12")
        p = self.run_it(t)
        self.assertRefused(p, "制御文字")


class NamesInTheOutputTest(E2E):
    """名前（workflow のファイル名・job・段）に行を崩しうる字があっても、 出力の 1 行に収まり、
    記録の行に似た行が独立しない（#77）。"""

    def test_names_are_escaped(self):
        fake = R.RECORD_PASS + "（偽の記録）"
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - name: "段\\u2028{fake}"
                    run: echo ran
                  - name: "向き\\u202e\\u0085{fake}"
                    run: echo ran
            """, name="b\u2029c.yml") | self.wf("on: push\njobs: {}\n", name="d\u2028e.yml"),
            {"a.txt": "x"}, name="t13")
        for extra in ((), ("--list",)):
            with self.subTest(extra=extra):
                p = self.run_it(t, *extra)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                lines = p.stdout.splitlines()                  # 復帰・U+0085・U+2028・U+2029 でも分ける
                forged = [ln for ln in lines if ln.lstrip().startswith(R.RECORD_PASS) and "偽の記録" in ln]
                self.assertEqual(forged, [], "記録の行に似た行が独立した")
                self.assertTrue(all(R.unicodedata.category(c) not in R.BREAKING for c in p.stdout
                                    if c not in "\n"), "行を崩しうる字が出力に残った")
                self.assertIn("b\\u2029c.yml", p.stdout)
                self.assertIn("対象外: d\\u2028e.yml", p.stdout)
                if not extra:
                    self.assertIn("段\\u2028" + R.RECORD_PASS, p.stdout)
                    self.assertIn("向き\\u202e\\u0085" + R.RECORD_PASS, p.stdout)
                    self.assertEqual(sum(ln.startswith(R.RECORD_PASS) for ln in lines), 1)

    def test_refusals_escape_names_too(self):
        """止まるときの文（stderr・終了コード 2）にも、 記録の行に似た行が独立しない（#77）。"""
        fake = R.RECORD_PASS + "（偽の記録）"
        cases = {
            "needs に無い job": self.wf(f"""\
                on: pull_request
                jobs:
                  j:
                    runs-on: ubuntu-latest
                    needs: ["n\\n{fake}"]
                    steps:
                      - run: echo ran
                """),
            "知らない鍵": self.wf(f"""\
                on: pull_request
                jobs:
                  j:
                    runs-on: ubuntu-latest
                    "k\\n{fake}": 1
                    steps:
                      - run: echo ran
                """),
            "流す workflow が無い": self.wf("on: push\njobs: {}\n", name=f"x\u2028{fake}.yml"),
        }
        for i, (name, files) in enumerate(cases.items()):
            with self.subTest(case=name):
                t = _repo(self.root, files, {"a.txt": "x"}, name=f"t14{i}")
                p = self.run_it(t)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                for out in (p.stdout, p.stderr):
                    self.assertEqual([ln for ln in out.splitlines() if ln.lstrip().startswith(R.RECORD_PASS)], [])

    def test_jobs_whose_headings_look_the_same_are_not_merged(self):
        """見出しが同じになる別の job（U+2028 のファイル名と、 文字どおり \\u2028 と書いたファイル名）を、
        1 つに数えない（結果の鍵は元の名前。 #77）。"""
        body = "on: pull_request\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo one\n"
        two = body + "      - run: echo two\n"
        t = _repo(self.root, self.wf(body, name="a\u2028.yml") | self.wf(two, name="a\\u2028.yml"),
                  {"a.txt": "x"}, name="t16")
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("workflow 2・job 2・段 3", p.stdout)

    def test_job_ids_that_yaml_reads_as_other_types_are_refused_with_a_reason(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              yes:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """), {"a.txt": "x"}, name="t17")
        self.assertRefused(self.run_it(t), "文字以外")

    def test_job_ids_must_be_the_github_form(self):
        """job の id は GitHub の形に限る。 `/` を含む id で、 再利用 workflow の中の job の鍵と重ねて、
        落ちた job の結果を上書きする道を閉じる（#77 の Reviewer B）。"""
        for i, jid in enumerate(('"a\\nb"', "'a\\u000ab'", '"x/y"', '"1abc"', '"a b"')):
            with self.subTest(jid=jid):
                t = _repo(self.root, self.wf(f"""\
                    on: pull_request
                    jobs:
                      {jid}:
                        runs-on: ubuntu-latest
                        steps:
                          - run: echo ran
                    """), {"a.txt": "x"}, name=f"t15{i}")
                p = self.run_it(t)
                self.assertRefused(p, "GitHub の形")
                self.assertEqual([ln for ln in p.stderr.splitlines() if ln.lstrip().startswith(R.RECORD_PASS)], [])

    def test_show_keeps_ordinary_names(self):
        self.assertEqual(R._show("検査 の job unit（3.10）"), "検査 の job unit（3.10）")
        self.assertEqual(R._show("a\tb\x7fc\ufeffd"), "a\\u0009b\\u007fc\\ufeffd")
        self.assertEqual(R._show("x\ud800y\U000e0001z"), "x\\ud800y\\U000e0001z")   # 孤立した surrogate・U+FFFF を超える書式の字


class EnvironmentTest(E2E):

    def test_home_is_isolated_and_only_listed_variables_are_passed(self):
        real_home = self.root / "real-home"
        real_home.mkdir()
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            env: {{A: from-workflow}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: |
                      test "$HOME" != "{real_home}"
                      test -z "${{GH_TOKEN:-}}${{SSH_AUTH_SOCK:-}}${{AWS_ACCESS_KEY_ID:-}}${{CLAUDE_X:-}}"
                      test -z "${{GIT_CONFIG_GLOBAL:-}}${{VSCODE_IPC_HOOK_CLI:-}}${{BROWSER:-}}${{https_proxy:-}}"
                      touch "$HOME/written-by-pr"
                      echo "FOO=bar" >> "$GITHUB_ENV"
                  - run: |
                      test "$FOO" = bar
                      test "${{{{ env.FOO }}}}" = bar
                      test "${{{{ env.A }}}}" = from-workflow
                      test -z "${{{{ env.HOME }}}}"
            """), {})
        p = self.run_it(t, env={"HOME": str(real_home), "GH_TOKEN": "dummy", "SSH_AUTH_SOCK": "/nowhere",
                                "AWS_ACCESS_KEY_ID": "dummy", "CLAUDE_X": "dummy",
                                "GIT_CONFIG_GLOBAL": "/nowhere", "VSCODE_IPC_HOOK_CLI": "/nowhere",
                                "BROWSER": "/nowhere", "https_proxy": "http://user:pass@proxy.invalid:1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((real_home / "written-by-pr").exists(), "本物の HOME に書かせない")


class ReusableWorkflowTest(E2E):

    def _canonical(self) -> Path:
        return _repo(self.root, {
            "tool.txt": "canonical",
            ".github/workflows/basic-checks-reusable.yml": f"""\
                on:
                  workflow_call:
                    inputs:
                      python-version: {{type: string, default: "{PYVER}"}}
                      flag: {{type: boolean, default: false}}
                jobs:
                  checks:
                    runs-on: ubuntu-latest
                    steps:
                      - uses: actions/checkout@v4
                        with: {{path: target}}
                      - uses: actions/checkout@v4
                        with: {{repository: MirumeAI/.github, path: validators}}
                      - uses: actions/setup-python@v5
                        with: {{python-version: "${{{{ inputs.python-version }}}}"}}
                      - env: {{CI_ROOT: "${{{{ github.workspace }}}}/target"}}
                        run: |
                          test -f "$CI_ROOT/marker.txt"
                          test "$(cat validators/tool.txt)" = canonical
                      - name: 守りの段
                        if: ${{{{ inputs.flag }}}}
                        run: exit 1
                """}, {}, name="canonical")

    def _caller(self, name: str, with_: str = "") -> Path:
        return _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              checks:
                uses: MirumeAI/.github/.github/workflows/basic-checks-reusable.yml@main
                {with_}
            """), {"marker.txt": "x"}, name=name)

    def test_reusable_workflow_runs_from_canonical_with_inputs(self):
        canon = self._canonical()
        p = self.run_it(self._caller("t1"), canonical=canon)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"05 skipped\s+守りの段")
        p = self.run_it(self._caller("t2", "with: {flag: true}"), canonical=canon)
        self.assertEqual(p.returncode, 1, "呼び出し側が true を渡したら、 守りの段が流れる")

    def test_caller_inputs_are_not_guessed(self):
        canon = self._canonical()
        self.assertRefused(self.run_it(self._caller(
            "t1", "with: {flag: \"${{ github.event_name == 'pull_request' }}\"}"), canonical=canon), "式")
        self.assertRefused(self.run_it(self._caller("t2", "with: {flag: yes-please}"), canonical=canon),
                           "true / false")


    def test_reusable_inner_job_ids_must_be_the_github_form(self):
        """再利用 workflow の中の job の id も、 GitHub の形に限る（#77 の Reviewer B）。"""
        canon = _repo(self.root, {
            ".github/workflows/basic-checks-reusable.yml": """\
                on:
                  workflow_call: {}
                jobs:
                  "in/ner":
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo ran
                """}, {}, name="canonical-bad")
        self.assertRefused(self.run_it(self._caller("t3"), canonical=canon), "GitHub の形")


class ExprAndGlobTest(unittest.TestCase):

    def test_expressions(self):
        ctx = {"github": {"event_name": "pull_request"}, "inputs": {"flag": True, "s": "Abc", "n": 2}, "env": {}}
        ex = R.Expr(ctx)
        self.assertTrue(ex.eval("github.event_name != 'push'"))
        self.assertTrue(ex.eval("inputs.s == 'abc'"))          # 文字は大文字小文字を区別しない
        self.assertFalse(ex.eval("!inputs.flag"))
        self.assertFalse(ex.eval("!inputs.n == true"))         # GitHub と同じく (!inputs.n) == true
        self.assertTrue(R._cond("${{ inputs.flag }}", ex))
        self.assertFalse(R._cond(None, R.Expr(ctx, "failure")))
        self.assertFalse(R._cond(None, R.Expr(ctx, "skipped")))
        self.assertTrue(R._cond("always()", R.Expr(ctx, "failure")))
        self.assertEqual(R._subst("a-${{ inputs.s }}-${{ inputs.flag }}", ex), "a-Abc-true")
        for bad in ("secrets.X", "hashFiles('x')", "github.head_ref"):
            with self.assertRaises(R.Unsupported):
                ex.eval(bad)

    def test_globs(self):
        self.assertTrue(R._matches("docs/a/b.md", ["docs/**"]))
        self.assertFalse(R._matches("a/b.md", ["*.md"]))
        self.assertTrue(R._matches("x.py", ["**/x.py"]))
        self.assertTrue(R._matches("a/b/x.py", ["**/x.py"]))
        self.assertFalse(R._matches("docs/skip.md", ["docs/**", "!docs/skip.md"]))
        with self.assertRaises(R.Unsupported):
            R._glob("docs/[ab].md")


if __name__ == "__main__":
    unittest.main()

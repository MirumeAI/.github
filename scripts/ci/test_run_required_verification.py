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
import signal
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
#: 同じ利用者の検査を重ねないロックの置き場を変える環境変数（#219。 道具の LOCK_ENV と同じことを試験で確かめる）
LOCK_ENV = "MIRUME_RV_LOCK_DIR"

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
        self.addCleanup(self._tmp.cleanup)  # 最後に消す（後で足す片付けは、 先に動く）
        self.root = Path(self._tmp.name)
        self.runs = 0
        # 同じ利用者の検査を重ねないロック（#219）は、 試験ごとの一時の置き場で取る。 本物の利用者の置き場に
        # 触れない（流している本物の検査を待たない・止めない）。 子の process にも渡る（os.environ を写す）
        from unittest import mock
        self.lock_dir = self.root / "lock"
        patcher = mock.patch.dict(os.environ, {LOCK_ENV: str(self.lock_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)

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


class DuplicateKeysTest(E2E):
    """**同じ鍵が 2 回ある workflow は、 何も流す前に止める**（#131）。

    YAML の読み手の既定は後の値で黙って上書きするので、 同じ job の id を 2 回書くと、
    先の落ちる job が消えて PASS の記録が出ていた。
    """

    def _refused(self, body: str, word: str = "同じ鍵が 2 回あります"):
        t = _repo(self.root, self.wf(body), {})
        p = self.run_it(t)
        self.assertRefused(p, word)
        self.assertNotIn(R.RECORD_PASS, p.stdout)

    def test_a_job_written_twice_does_not_hide_the_failing_one(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """)

    def test_a_key_written_twice_in_a_step_is_refused(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
                    run: echo ok
            """)

    def test_a_top_level_key_written_twice_is_refused(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """)

    def test_a_reusable_workflow_with_a_key_written_twice_is_refused(self):
        canon = _repo(self.root, self.wf("""\
            on: {workflow_call: {}}
            jobs:
              c:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              c:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """, "common.yml"), {}, name="canon")
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              common:
                uses: MirumeAI/.github/.github/workflows/common.yml@main
            """), {})
        p = self.run_it(t, canonical=canon)
        self.assertRefused(p, "同じ鍵が 2 回あります")
        self.assertIn("common.yml", p.stderr)

    def test_broken_yaml_stops_without_a_traceback(self):
        t = _repo(self.root, self.wf("on: pull_request\njobs: [\n"), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("workflow を読めません", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_on_and_quoted_on_hide_nothing(self):
        """`on:` は YAML 1.1 で True になり、 `"on":` とは別の鍵に読める。 GitHub では同じ鍵。"""
        t = _repo(self.root, {
            ".github/workflows/new.yml": """\
                on: pull_request
                "on": push
                jobs:
                  j:
                    runs-on: ubuntu-latest
                    steps:
                      - run: exit 1
                """,
            ".github/workflows/ci.yml": """\
                on: pull_request
                jobs:
                  ok:
                    runs-on: ubuntu-latest
                    steps:
                      - run: echo ok
                """}, {})
        p = self.run_it(t)
        self.assertRefused(p, "同じ鍵が 2 回あります: 'on'")
        self.assertNotIn(R.RECORD_PASS, p.stdout)

    def test_a_job_written_twice_through_a_merge_key_is_refused(self):
        self._refused("""\
            on: pull_request
            jobs:
              <<: {j: {runs-on: ubuntu-latest, steps: [{run: exit 1}]}}
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """, "合わせる鍵（<<）は使えません")

    def test_merge_keys_are_refused_in_every_form(self):
        for text in (
                # 合わせる中で鍵が重なる
                "on: pull_request\njobs:\n  <<: {j: {runs-on: x, steps: [{run: exit 1}]}, "
                "j: {runs-on: x, steps: [{run: echo}]}}\n",
                # <<: を 2 回書く
                "on: pull_request\njobs:\n  <<: {j: {runs-on: x, steps: [{run: exit 1}]}}\n"
                "  <<: {j: {runs-on: x, steps: [{run: echo}]}}\n",
                # 段の中で、 合わせた run を明示の run が上書きする
                "on: pull_request\njobs:\n  j:\n    runs-on: x\n    steps:\n"
                "      - <<: {run: exit 1}\n        run: echo ok\n"):
            with self.subTest(text=text), self.assertRaises(R.Unsupported) as cm:
                R._load_yaml(text, "w.yml")
            self.assertRegex(str(cm.exception), "合わせる鍵|同じ鍵が 2 回")

    def test_values_that_cannot_be_read_stop_without_a_traceback(self):
        for text in ("on: pull_request\njobs: !!int abc\n", "on: pull_request\nx: 2026-02-30\n"):
            with self.subTest(text=text), self.assertRaises(R.Unsupported):
                R._load_yaml(text, "w.yml")

    def test_distinct_keys_and_whole_node_aliases_still_load(self):
        d = R._load_yaml(textwrap.dedent("""\
            on: pull_request
            x: &steps [{run: echo}]
            jobs:
              a: {runs-on: ubuntu-latest, steps: *steps}
              b: {runs-on: ubuntu-latest, steps: *steps}
            """), "w.yml")
        self.assertEqual(sorted(d["jobs"]), ["a", "b"])
        self.assertEqual(d["jobs"]["b"]["steps"], [{"run": "echo"}])


class Backlog121Test(E2E):
    """#114・#133 の Reviewer の Backlog（#121）。"""

    def test_a_non_string_inner_job_id_asks_for_quotes(self):
        canon = _repo(self.root, self.wf("""\
            on: {workflow_call: {}}
            jobs:
              1:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """, "common.yml"), {}, name="canon")
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              common:
                uses: MirumeAI/.github/.github/workflows/common.yml@main
            """), {})
        self.assertRefused(self.run_it(t, canonical=canon), "引用符で囲んでください")

    def test_a_lone_surrogate_in_a_step_name_is_written_to_the_summary(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - name: "a\\ud800b"
                    run: echo ok
            """), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(R.RECORD_PASS, p.stdout)
        self.assertIn("a\\ud800b", p.stdout)
        import json
        summary = json.loads((self.work / "logs" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["jobs"][0]["steps"][0]["name"], "a\ud800b")

    def test_explicit_yaml_types_are_refused(self):
        for text in ("on: !!omap [{pull_request: {}}]\njobs: {}\n",
                     "on: pull_request\njobs: !!omap [{j: {runs-on: x}}]\n",
                     "on: pull_request\nx: !!pairs [{a: 1}]\n",
                     "on: pull_request\nx: !!set {a: null}\n",
                     "on: pull_request\nx: !!binary aGk=\n"):
            with self.subTest(text=text):
                with self.assertRaises(R.Unsupported) as cm:
                    R._load_yaml(text, "w.yml")
                self.assertIn("YAML の型", str(cm.exception))
        # 型の推し量り（真偽値・数・日付）は今までどおり読む
        d = R._load_yaml("on: pull_request\nx: {a: true, b: 1, c: 2026-10-31}\n", "w.yml")
        self.assertEqual(d["x"]["a"], True)

    def test_a_backslash_is_shown_apart_from_an_escaped_character(self):
        self.assertEqual(R._show("a\\b"), "a\\\\b")
        self.assertNotEqual(R._show("x\\u2028y"), R._show("x y"))
        self.assertEqual(R._show("x y"), "x\\u2028y")

    def test_an_unexpected_error_stops_on_one_line_without_a_traceback(self):
        import contextlib
        import io
        from unittest import mock
        err = io.StringIO()
        with mock.patch.object(R, "_bootstrap_yaml", side_effect=RuntimeError("壊れた\n" + R.RECORD_PASS)), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = R.main([str(self.root)])
        self.assertEqual(rc, 2)
        text = err.getvalue()
        self.assertIn("道具の中の誤り", text)
        self.assertNotIn("Traceback", text)
        self.assertEqual(len(text.strip().splitlines()), 1, "1 行で止まる（記録の行を作らない）")

    def test_an_unexpected_error_after_jobs_started_is_a_fail_not_unrunnable(self):
        # 落ちた job の後の誤りを「流せない（2）」にすると、 FAIL が代えのきく側に隠れる（#134 の B）
        import contextlib
        import io
        import re
        from unittest import mock
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: exit 1
              b:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {})
        failed = {"status": "failure", "steps": [{"n": 1, "name": "x", "result": "failed", "secs": 0.0,
                                                  "ran": 0, "skipped_tests": 0}]}
        # パスワードに encode していない @ があっても、 host の前の最後の @ まで伏せる
        broken = RuntimeError("壊れた https://u:se@cret@example.invalid/x\n" + R.RECORD_PASS)
        for name, effects in (("落ちた job の後", [failed, broken]), ("最初の job で", [broken])):
            with self.subTest(name):
                out, err = io.StringIO(), io.StringIO()
                # mock の job は job の場所を作らないので、 片付けも止めて、 入れた誤りが起きるようにする
                with mock.patch.object(R, "_run_job", side_effect=effects), mock.patch.object(R, "_rmtree"), \
                        contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    # 1 つずつ流す（mock の返す値の順は、 job を流す順に頼る）
                    rc = R.main([str(t), "--head", "pr", "--base", "main", "--canonical", str(t),
                                 "--canonical-ref", "main", "--jobs", "1", "--work", str(self.root / f"work-{name}")])
                text = out.getvalue() + err.getvalue()
                self.assertEqual(rc, 1, text)
                self.assertRegex(text, r"(?m)^結果: FAIL（job を流し始めた後に道具の中の誤りで止めました: RuntimeError: 壊れた")
                self.assertNotRegex(text, rf"(?m)^\s*{re.escape(R.RECORD_PASS)}")
                self.assertNotIn("Traceback", text)
                self.assertNotIn("cret", text, "URL の資格情報を出さない")


def _meet(me: str, other: str, at: Path, wait_s: int = 20) -> str:
    """job の段: 自分が始まった印を置き、 相手の印を待つ（来れば together、 来なければ alone）。
    待ち合わせで重なりを確かめる（短い待ちの時間で確かめない）。"""
    return (f'touch "{at}/{me}.started"; r=alone; for i in $(seq {wait_s * 10}); do '
            f'if [ -e "{at}/{other}.started" ]; then r=together; break; fi; sleep 0.1; done; '
            f'echo $r > "{at}/{me}.result"')


class ParallelJobsTest(E2E):
    """待ち合わせの無い job は `--jobs` まで同時に流す（#149）。 `needs` の順と結果の表示の順は変えない。"""

    def two(self, at: Path | None = None, wait_s: int = 20) -> Path:
        """待ち合わせる 2 つの job。 印は at（既定 self.root）に置く。"""
        at = at or self.root
        return _repo(at, self.wf(f"""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: {_meet("a", "b", at, wait_s)}
              b:
                runs-on: ubuntu-latest
                steps:
                  - run: {_meet("b", "a", at, wait_s)}
            """), {})

    def test_independent_jobs_run_at_the_same_time(self):
        p = self.run_it(self.two(), "--jobs", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.root / "a.result").read_text().strip(), "together")
        self.assertEqual((self.root / "b.result").read_text().strip(), "together")

    def in_process(self, target: Path, *extra, cpus: int, mem: int | None) -> tuple:
        """CPU の数と空きメモリを決めて、 同じ process で流す（既定の `--jobs` を機械によらずに確かめる。 #219）。"""
        import contextlib
        import io
        from unittest import mock
        self.runs += 1
        out = io.StringIO()
        with mock.patch.object(R, "_cpus", return_value=cpus), \
                mock.patch.object(R, "_available_memory", return_value=mem), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = R.main([str(target), "--head", "pr", "--base", "main", "--canonical", str(target),
                         "--canonical-ref", "main", "--python", f"{PYVER}={sys.executable}",
                         "--work", str(self.root / f"work-{self.runs}"), *extra])
        return rc, out.getvalue()

    def test_the_default_runs_jobs_at_the_same_time(self):
        rc, out = self.in_process(self.two(), cpus=16, mem=64 * 1024 ** 3)
        self.assertEqual(rc, 0, out)
        self.assertEqual((self.root / "a.result").read_text().strip(), "together")
        self.assertIn("同時に流す job の数: 2（--jobs の既定。 使える CPU 16 − 2 = 14・空きメモリ 64.0 GiB", out)
        self.assertIn("job ごとに 8 に絞ります", out)

    def test_the_default_runs_one_at_a_time_on_a_small_machine(self):
        """CPU が少ない・空きメモリが少ない機械では、 既定で 1 つずつ流す（スレッドも絞らない）。"""
        for name, cpus, mem in (("CPU", 3, 64 * 1024 ** 3), ("メモリ", 16, R.JOB_MEMORY * 3 // 2)):
            with self.subTest(name):
                at = self.root / f"small-{self.runs}"
                at.mkdir()
                rc, out = self.in_process(self.two(at, wait_s=3), cpus=cpus, mem=mem)
                self.assertEqual(rc, 0, out)
                self.assertIn("同時に流す job の数: 1（--jobs の既定", out)
                self.assertEqual((at / "a.result").read_text().strip(), "alone")
                self.assertNotIn("に絞ります", out)

    def test_the_default_shows_the_cpus_of_this_machine(self):
        """mock なしでも、 決めた数と根拠（この機械の使える CPU の数）を出す。 記録の行には出さない。"""
        p = self.run_it(self.two())
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"（--jobs の既定。 使える CPU {len(os.sched_getaffinity(0))} − 2 = ", p.stdout)
        record = [ln for ln in p.stdout.splitlines() if ln.startswith(R.RECORD_PASS)]
        self.assertEqual(len(record), 1, p.stdout)
        self.assertNotIn("CPU", record[0])
        self.assertNotIn("jobs", record[0])

    def test_an_explicit_jobs_is_used_as_given(self):
        """`--jobs N` を渡せば、 今までどおりその数。 既定の根拠の行は出さない。"""
        rc, out = self.in_process(self.two(), "--jobs", "2", cpus=1, mem=None)   # 既定なら 1 つずつになる機械
        self.assertEqual(rc, 0, out)
        self.assertNotIn("--jobs の既定", out)
        self.assertEqual((self.root / "a.result").read_text().strip(), "together")

    def test_jobs_1_runs_one_at_a_time(self):
        p = self.run_it(self.two(), "--jobs", "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.root / "a.result").read_text().strip(), "alone",
                         "1 つずつなら、 a が終わるまで b は始まらない")

    def test_needs_and_the_order_of_the_output_are_kept(self):
        """b は a の後（needs）。 c は a と同時に流れ、 先に終わっても、 表示は plan の順（a・b・c）。"""
        at = self.root
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: {_meet("a", "c", at)}; for i in $(seq 200); do [ -e "{at}/c.done" ] && break; sleep 0.1; done; touch "{at}/a.done"
              b:
                runs-on: ubuntu-latest
                needs: a
                steps:
                  - run: test -e "{at}/a.done"
              c:
                runs-on: ubuntu-latest
                steps:
                  - run: {_meet("c", "a", at)}; touch "{at}/c.done"
            """), {})
        p = self.run_it(t, "--jobs", "3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((at / "a.result").read_text().strip(), "together")
        out = p.stdout
        self.assertLess(out.index("の job a: PASS"), out.index("の job b: PASS"))
        self.assertLess(out.index("の job b: PASS"), out.index("の job c: PASS"))

    def test_a_failed_job_still_stops_its_dependents(self):
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
              c:
                runs-on: ubuntu-latest
                steps:
                  - run: echo c
            """), {})
        p = self.run_it(t, "--jobs", "3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("の job b: 前の job が落ちたので流さない", p.stdout)
        self.assertIn("の job c: PASS", p.stdout)

    def test_job_concurrency_runs_one_at_a_time(self):
        """job ごとの concurrency があれば、 GitHub が同じ組を同時に流さないので、 1 つずつ流す（#149 の B）。"""
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                concurrency: one
                steps:
                  - run: {_meet("a", "b", self.root, 5)}
              b:
                runs-on: ubuntu-latest
                concurrency: one
                steps:
                  - run: {_meet("b", "a", self.root, 5)}
            """), {})
        p = self.run_it(t, "--jobs", "3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("job ごとの concurrency があるので", p.stdout)
        self.assertEqual((self.root / "a.result").read_text().strip(), "alone")

    def threads(self, env: str = "") -> Path:
        at = self.root
        step = f"""- run: echo "${{OMP_NUM_THREADS:-none}} ${{OPENCV_FOR_THREADS_NUM:-none}}" > "{at}/$NAME.threads\""""
        return _repo(self.root, self.wf(f"""\
            on: pull_request
            {env}
            jobs:
              a:
                runs-on: ubuntu-latest
                env: {{NAME: a}}
                steps:
                  {step}
              b:
                runs-on: ubuntu-latest
                env: {{NAME: b}}
                steps:
                  {step}
            """), {})

    def test_threads_are_limited_when_jobs_run_together(self):
        """同時に流すときは、 計算の部品のスレッドを「CPU の数 ÷ 同時に流す数」に絞る。"""
        p = self.run_it(self.threads(), "--jobs", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        want = str(max(1, len(os.sched_getaffinity(0)) // 2))
        self.assertEqual((self.root / "a.threads").read_text().split(), [want, want])
        self.assertIn(f"job ごとに {want} に絞ります", p.stdout)

    def test_threads_use_the_number_of_jobs_that_can_run_together(self):
        """--jobs が job の数より多いときは、 同時に流れる数（job の数）で割る。"""
        p = self.run_it(self.threads(), "--jobs", "8")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        want = str(max(1, len(os.sched_getaffinity(0)) // 2))
        self.assertEqual((self.root / "b.threads").read_text().split(), [want, want])

    def test_threads_are_not_limited_one_at_a_time(self):
        p = self.run_it(self.threads(), "--jobs", "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.root / "a.threads").read_text().split(), ["none", "none"])
        self.assertNotIn("に絞ります", p.stdout)

    def test_the_workflow_env_wins_over_the_limit(self):
        p = self.run_it(self.threads('env: {OMP_NUM_THREADS: "1"}'), "--jobs", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.root / "a.threads").read_text().split()[0], "1")

    def test_cpus_without_sched_getaffinity(self):
        """`sched_getaffinity` の無い OS（macOS など）でも、 CPU の数を読める。"""
        from unittest import mock
        real = os.sched_getaffinity
        del os.sched_getaffinity
        self.addCleanup(setattr, os, "sched_getaffinity", real)
        with mock.patch.object(os, "cpu_count", return_value=7):
            self.assertEqual(R._cpus(), 7)
        with mock.patch.object(os, "cpu_count", return_value=None):
            self.assertEqual(R._cpus(), 1)

    def test_one_at_a_time_does_not_read_the_cpus(self):
        """1 つずつ流すときは、 CPU の数を読まない（読めなくても流せる）。"""
        import contextlib
        import io
        from unittest import mock
        t = self.threads()
        out = io.StringIO()
        with mock.patch.object(R, "_cpus", side_effect=RuntimeError("読まない")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = R.main([str(t), "--head", "pr", "--base", "main", "--canonical", str(t), "--canonical-ref", "main",
                         "--jobs", "1", "--list"])
        self.assertEqual(rc, 0, out.getvalue())

    def test_jobs_must_be_positive(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo a
            """), {})
        self.work = self.root / "work-0"
        p = subprocess.run([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main",
                            "--canonical", str(t), "--canonical-ref", "main", "--jobs", "0",
                            "--work", str(self.work)], capture_output=True, text=True, timeout=120)
        self.assertRefused(p, "--jobs は 1 以上")


class StoppedTest(E2E):
    """SIGTERM で止めると、 流している段の process を止め、 次の段を始めず、 作業の場所を消す（#149）。"""

    def test_sigterm_stops_the_running_steps_and_cleans_up(self):
        at = self.root
        body = "\n".join(f"""\
              {n}:
                runs-on: ubuntu-latest
                steps:
                  - run: sleep 300 & echo $! > "{at}/{n}.pid"; wait
                  - if: always()
                    run: touch "{at}/{n}.after"
            """ for n in ("a", "b"))
        t = _repo(self.root, self.wf("on: pull_request\njobs:\n" + textwrap.indent(textwrap.dedent(body), "  ")), {})
        self.work = self.root / "work-term"
        proc = subprocess.Popen([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main",
                                 "--canonical", str(t), "--canonical-ref", "main", "--jobs", "2",
                                 "--work", str(self.work)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            pids = {}
            for _ in range(1200):         # 2 つの段が始まるまで（始まらなければ、 下で落ちる）
                for n in ("a", "b"):
                    f = at / f"{n}.pid"
                    if n not in pids and f.exists() and f.read_text().strip():
                        pids[n] = int(f.read_text())
                if len(pids) == 2 or proc.poll() is not None:
                    break
                time.sleep(0.1)
            self.assertEqual(sorted(pids), ["a", "b"], "2 つの job の段が同時に始まらない")
            proc.send_signal(signal.SIGTERM)
            out, err = proc.communicate(timeout=120)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
        self.assertEqual(proc.returncode, 128 + signal.SIGTERM, out + err)
        self.assertIn("止めました", err)
        self.assertNotIn(R.RECORD_PASS, out)
        for n, pid in pids.items():
            for _ in range(50):
                if not _alive(pid):
                    break
                time.sleep(0.1)
            self.assertFalse(_alive(pid), f"{n} の段の process が残った")
            self.assertFalse((at / f"{n}.after").exists(), "止めた後に次の段を始めた")
        self.assertEqual(sorted(x.name for x in self.work.iterdir() if x.name.startswith("job-")), [],
                         "job の作業の場所を消していない")

    def test_a_previous_stop_does_not_carry_over(self):
        """同じ process で前に止めても、 次の main() は段を流す（止めた印を持ち越さない）。"""
        import contextlib
        import io
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {})
        R._stop_all()
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = R.main([str(t), "--head", "pr", "--base", "main", "--canonical", str(t), "--canonical-ref", "main",
                         "--work", str(self.root / "work-again")])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertIn(R.RECORD_PASS, out.getvalue())

    def test_a_second_signal_does_not_cut_the_cleanup_short(self):
        """1 回目の合図の後に、 すぐ 2 回目（SIGTERM・SIGINT）が届いても、 段を止めて作業の場所を消し終える。"""
        for second in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(second=second.name):
                at = self.root / second.name
                at.mkdir()
                # 作業の場所を大きくして、 消している間に 2 回目が届くようにする
                t = _repo(at, self.wf(f"""\
                    on: pull_request
                    jobs:
                      a:
                        runs-on: ubuntu-latest
                        steps:
                          - run: |
                              mkdir -p "$HOME/many" && cd "$HOME/many" && seq 20000 | xargs touch
                              sleep 300 & echo $! > "{at}/a.pid"; wait
                    """), {})
                self.work = at / "work"
                proc = subprocess.Popen([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main",
                                         "--canonical", str(t), "--canonical-ref", "main", "--work", str(self.work)],
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    pid = None
                    for _ in range(1200):
                        f = at / "a.pid"
                        if f.exists() and f.read_text().strip():
                            pid = int(f.read_text())
                            break
                        if proc.poll() is not None:
                            break
                        time.sleep(0.1)
                    self.assertIsNotNone(pid, "段が始まらない")
                    proc.send_signal(signal.SIGTERM)
                    # 片付けの間（job の作業の場所が残っている間）、 2 回目を送り続ける。 どこで届いても、
                    # 途中でやめないこと。 消し終えたら送るのをやめる（道具の終わりの処理の中で届かせない）
                    for _ in range(12000):
                        if proc.poll() is not None or not any(self.work.glob("job-*")):
                            break
                        proc.send_signal(second)
                        time.sleep(0.01)
                    out, err = proc.communicate(timeout=120)
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()
                # 2 つの合図が Python に渡る前に重なると、 番号の小さい SIGINT を先に受ける（どちらで止めても正しい）
                self.assertIn(proc.returncode, {128 + signal.SIGTERM, 128 + second}, out + err)
                self.assertIn("止めました", err)
                self.assertNotIn("Traceback", err)
                for _ in range(50):
                    if not _alive(pid):
                        break
                    time.sleep(0.1)
                self.assertFalse(_alive(pid), "段の process が残った")
                self.assertEqual([x.name for x in self.work.iterdir() if x.name.startswith("job-")], [],
                                 "2 回目の合図で、 job の作業の場所を消すのを途中でやめた")


class DefaultJobsTest(unittest.TestCase):
    """`--jobs` の既定は、 使える CPU の数 − 2 と、 空きメモリ ÷ 1 job の見込み の小さい方。 1 以上で、 plan の job の数まで（#219）。"""

    G = 1024 ** 3

    def default(self, cpus: int, mem, n_jobs: int) -> tuple:
        from unittest import mock
        with mock.patch.object(R, "_cpus", return_value=cpus), \
                mock.patch.object(R, "_available_memory", return_value=mem):
            return R._default_jobs(n_jobs)

    def test_the_smaller_of_cpus_and_memory_up_to_the_plan(self):
        per = R.JOB_MEMORY
        cases = (((16, 64 * self.G, 20), 14),        # CPU で決まる（2 つ残す）
                 ((16, 64 * self.G, 12), 12),        # plan の job の数まで
                 ((16, 5 * per + 1, 20), 5),         # 空きメモリで決まる
                 ((16, per - 1, 20), 1),             # 空きメモリが 1 job に足りなくても 1
                 ((2, 64 * self.G, 20), 1),          # CPU が 2 以下でも 1
                 ((3, 64 * self.G, 20), 1),
                 ((8, None, 20), 6),                 # 空きメモリが読めなければ CPU だけ
                 ((16, 64 * self.G, 1), 1))
        for args, want in cases:
            with self.subTest(args=args):
                self.assertEqual(self.default(*args)[0], want)

    def test_the_reason_names_the_cpus_and_the_memory(self):
        _, why = self.default(16, 54 * self.G, 20)
        self.assertIn("使える CPU 16 − 2 = 14", why)
        self.assertIn(f"空きメモリ 54.0 GiB ÷ {R.JOB_MEMORY / self.G:g} GiB = {54 * self.G // R.JOB_MEMORY}", why)
        _, why = self.default(16, None, 20)
        self.assertIn("空きメモリは読めない", why)

    def test_available_memory_is_read_from_meminfo(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "meminfo"
            f.write_text("MemTotal:       65768900 kB\nMemFree:  1 kB\nMemAvailable:   56413276 kB\n")
            self.assertEqual(R._available_memory(f), 56413276 * 1024)
            for text in ("MemTotal: 1 kB\n", "MemAvailable: x kB\n", "MemAvailable: 5 MB\n", "MemAvailable:\n", ""):
                with self.subTest(text=text):
                    f.write_text(text)
                    self.assertIsNone(R._available_memory(f))
            self.assertIsNone(R._available_memory(Path(d) / "none"))

    def test_this_machine_can_be_read(self):
        if Path("/proc/meminfo").exists():
            self.assertGreater(R._available_memory(), 0)
        n, _ = R._default_jobs(1000)
        self.assertGreaterEqual(n, 1)


def _hold(me: str, at: Path) -> str:
    """job の段: 始まった印（段の process の組の番号）を置き、 at/release が来るまで待つ（最大 120 秒）。"""
    return (f'echo $$ > "{at}/{me}.started"; for i in $(seq 1200); do [ -e "{at}/release" ] && break; '
            f'sleep 0.1; done; touch "{at}/{me}.done"')


class LockTest(E2E):
    """同じ利用者の検査を重ねない（#219）。 2 本目は 1 本目の終わりを待ち、 `--no-wait` なら止まる。 止めた・落ちた
    ときにロックを残さない。 ロックは試験ごとの一時の置き場（E2E の setUp）で取る。"""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.cleanup_steps)

    def cleanup_steps(self):
        """後ろに残った段（道具を SIGKILL した試験など）を止める。"""
        (self.root / "release").touch()
        for f in self.root.glob("*.started"):
            try:
                os.killpg(int(f.read_text()), signal.SIGKILL)
            except (ValueError, ProcessLookupError, PermissionError):
                pass

    def start(self, name: str, run: str, *extra):
        """1 つの job の検査を後ろで流し始める。 出力（stdout と stderr）はファイルに書く。"""
        t = _repo(self.root, self.wf(f"""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: {run}
            """), {}, name=name)
        log = self.root / f"{name}.out"
        fh = open(log, "w", encoding="utf-8")
        self.addCleanup(fh.close)
        proc = subprocess.Popen([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main",
                                 "--canonical", str(t), "--canonical-ref", "main",
                                 "--python", f"{PYVER}={sys.executable}", "--work", str(self.root / f"work-{name}"),
                                 *extra], stdout=fh, stderr=subprocess.STDOUT)
        self.addCleanup(self.finish, proc)
        return proc, log, _git(t, "rev-parse", "pr")

    @staticmethod
    def finish(proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait()

    def wait_for(self, cond, what: str, timeout: float = 120):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if cond():
                return
            time.sleep(0.1)
        self.fail(f"{what} が起きない")

    def first(self):
        """1 本目: 段が始まり、 at/release まで終わらない検査。"""
        proc, log, head = self.start("first", _hold("first", self.root))
        self.wait_for(lambda: (self.root / "first.started").exists() and (self.root / "first.started").read_text().strip(),
                      "1 本目の段の始まり")
        return proc, log, head

    def hold_lock(self, record: bytes) -> int:
        """試験の process でロックを持つ（record を持ち主の記録として書く）。 閉じれば外れる。"""
        import fcntl
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_dir / R.LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.pwrite(fd, record, 0)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def assertLockIsFree(self):
        import fcntl
        fd = os.open(self.lock_dir / R.LOCK_NAME, os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # 取れなければ BlockingIOError で落ちる
        finally:
            os.close(fd)

    def test_the_lock_env_is_the_one_the_tests_use(self):
        self.assertEqual(R.LOCK_ENV, LOCK_ENV)
        self.assertEqual(R._lock_path(), self.lock_dir / R.LOCK_NAME)

    def test_the_default_place_is_under_the_cache_of_the_user(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "c")}):
            os.environ.pop(R.LOCK_ENV)
            self.assertEqual(R._lock_path(), self.root / "c" / "mirumeai" / R.LOCK_NAME)

    def test_a_second_run_waits_for_the_first(self):
        at = self.root
        first, _, head1 = self.first()
        second, log2, _ = self.start(
            "second", f'if [ -e "{at}/first.done" ]; then echo after; else echo before; fi > "{at}/second.order"')
        self.wait_for(lambda: "終わるまで待ちます" in log2.read_text(encoding="utf-8"), "2 本目の待ちの知らせ")
        time.sleep(1.5)                     # 待ちの間に、 2 本目の job が始まらないこと
        self.assertIsNone(second.poll())
        self.assertFalse((at / "second.order").exists(), "1 本目が終わる前に 2 本目の job が始まった")
        self.assertFalse((at / "work-second").exists(), "待つ間に作業の場所を作った")
        text = log2.read_text(encoding="utf-8")
        waiting = [ln for ln in text.splitlines() if "終わるまで待ちます" in ln]
        self.assertEqual(len(waiting), 1, text)
        self.assertIn(f"（local/first・head {head1[:12]}・", waiting[0])
        self.assertRegex(waiting[0], r"・\d{4}-\d\d-\d\d \d\d:\d\d:\d\d に始めた・process \d+）")
        self.assertIn("--no-wait", waiting[0])
        self.assertNotIn(str(self.root), waiting[0], "道を出さない")
        (at / "release").touch()
        self.assertEqual(first.wait(120), 0)
        self.assertEqual(second.wait(120), 0, log2.read_text(encoding="utf-8"))
        self.assertEqual((at / "second.order").read_text().strip(), "after")
        text = log2.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^待ち終えました（\d+ 秒）$")
        self.assertRegex(text, rf"(?m)^{R.RECORD_PASS}（")
        self.assertLockIsFree()
        self.assertEqual((self.lock_dir / R.LOCK_NAME).read_bytes(), b"", "終わったら持ち主の記録を消す")

    def test_no_wait_stops_while_another_runs(self):
        at = self.root
        first, _, head1 = self.first()
        second, log2, _ = self.start("second", f'touch "{at}/second.ran"', "--no-wait")
        self.assertEqual(second.wait(120), 2, log2.read_text(encoding="utf-8"))
        text = log2.read_text(encoding="utf-8")
        self.assertRegex(text, rf"(?m)^流せません: 同じ利用者の別の検査が流れています（local/first・head {head1[:12]}・"
                               r".*）。 --no-wait なので待たずに止めます$")
        self.assertNotIn("終わるまで待ちます", text)
        self.assertNotIn(R.RECORD_PASS, text)
        self.assertFalse((at / "second.ran").exists())
        self.assertFalse((at / "work-second").exists(), "止めるときは作業の場所を作らない")
        (at / "release").touch()
        self.assertEqual(first.wait(120), 0)

    def test_the_lock_is_not_left_when_the_run_is_stopped(self):
        """流している検査を SIGTERM で止めると、 すぐ次の検査が流せる。"""
        at = self.root
        first, log1, _ = self.first()
        first.send_signal(signal.SIGTERM)
        self.assertEqual(first.wait(120), 128 + signal.SIGTERM, log1.read_text(encoding="utf-8"))
        self.assertLockIsFree()
        second, log2, _ = self.start("second", "echo ok", "--no-wait")
        self.assertEqual(second.wait(120), 0, log2.read_text(encoding="utf-8"))
        self.assertFalse((at / "first.done").exists())

    def test_the_lock_is_not_left_when_the_tool_dies(self):
        """道具が落ちても（SIGKILL。 後片付けの処理は動かない）、 OS がロックを外す。 段の process は、 道具が止まらず
        後ろに残っても、 ロックを持たない（fd は子に渡らない）。"""
        at = self.root
        first, _, _ = self.first()
        step = int((at / "first.started").read_text())
        first.kill()
        first.wait(120)
        self.assertTrue(_alive(step), "段の process が残っていない（この試験の前提）")
        self.assertLockIsFree()
        second, log2, _ = self.start("second", "echo ok", "--no-wait")
        self.assertEqual(second.wait(120), 0, log2.read_text(encoding="utf-8"))

    def test_stopping_a_waiting_run_leaves_the_holder_alone(self):
        """待っている検査を止めても（SIGTERM・Ctrl-C）、 traceback を出さず、 何も流さず、 持ち主の記録を消さない。"""
        at = self.root
        first, _, head1 = self.first()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(sig=sig.name):
                name = f"waiter-{sig.name}"
                w, logw, _ = self.start(name, f'touch "{at}/{name}.ran"')
                self.wait_for(lambda: "終わるまで待ちます" in logw.read_text(encoding="utf-8"), "待ちの知らせ")
                w.send_signal(sig)
                self.assertEqual(w.wait(120), 128 + sig, logw.read_text(encoding="utf-8"))
                text = logw.read_text(encoding="utf-8")
                self.assertIn("止めました（job を流す前。 何も流していません）", text)
                self.assertNotIn("Traceback", text)
                self.assertFalse((at / f"{name}.ran").exists())
                self.assertFalse((at / f"work-{name}").exists())
        third, log3, _ = self.start("third", "echo ok", "--no-wait")
        self.assertEqual(third.wait(120), 2)
        self.assertIn(f"（local/first・head {head1[:12]}・", log3.read_text(encoding="utf-8"),
                      "待っていた検査が、 持ち主の記録を消した")
        (at / "release").touch()
        self.assertEqual(first.wait(120), 0)

    def test_list_and_refusals_do_not_take_the_lock(self):
        """`--list` と、 流す前に止まるとき（知らない書き方）は、 ロックを待たない。"""
        fd = self.hold_lock(b"{}")
        self.addCleanup(os.close, fd)
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - uses: example/unknown@v1
            """), {})
        p = subprocess.run([sys.executable, str(RUNNER), str(t), "--head", "pr", "--base", "main", "--canonical",
                            str(t), "--canonical-ref", "main", "--work", str(self.root / "work-x")],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("流せません", p.stderr)
        self.assertNotIn("待ちます", p.stdout + p.stderr)
        self.assertFalse((self.root / "work-x").exists())
        ok = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {}, name="ok")
        p = subprocess.run([sys.executable, str(RUNNER), str(ok), "--head", "pr", "--base", "main", "--canonical",
                            str(ok), "--canonical-ref", "main", "--list"], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("待ちます", p.stdout + p.stderr)

    def test_the_record_of_the_holder_cannot_break_the_output(self):
        """持ち主の記録は同じ利用者のほかの process が書くもの。 行を崩す字・形の違う値・読めない記録でも、
        記録の行に似た行を作らず、 道や値をそのまま出さない。"""
        import json
        ok = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {})
        records = {
            "崩す字": json.dumps({"repository": "x\n" + R.RECORD_PASS + "（", "head": "/home/user/secret",
                                 "started": "now\nx", "pid": "1"}).encode(),
            "読めない": b"\xff\xfe not json",
            "長い": json.dumps({"repository": "r" * 3000, "head": "a" * 40}).encode(),
        }
        for name, record in records.items():
            with self.subTest(name):
                fd = self.hold_lock(record)
                try:
                    p = self.run_it(ok, "--no-wait")
                finally:
                    os.close(fd)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertNotRegex(p.stdout + p.stderr, rf"(?m)^\s*{R.RECORD_PASS}")
                self.assertNotIn("/home/user", p.stderr)
                line = [ln for ln in p.stderr.splitlines() if "別の検査が流れています" in ln]
                self.assertEqual(len(line), 1, p.stderr)
                self.assertLess(len(line[0]), 400)
                if name == "崩す字":
                    self.assertIn("x\\u000a" + R.RECORD_PASS, line[0])
                    self.assertIn("head は不明・始めた時刻は不明）", line[0])
                if name == "読めない":
                    self.assertIn("ロックを持つ検査の記録を読めません", line[0])
                if name == "長い":
                    self.assertIn("r" * 100 + f"・head {'a' * 12}・", line[0])
                    self.assertNotIn("r" * 101, line[0])

    def test_the_lock_is_held_while_jobs_run_and_released_after_an_unexpected_error(self):
        """job を流す間は持ち主の記録を書いてロックを持ち、 道具の中の誤りで止まっても外す（同じ process で
        次の main() も流せる）。"""
        import contextlib
        import fcntl
        import io
        import json
        from unittest import mock
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {})
        seen = {}

        def broken(*_args, **_kw):
            fd = os.open(self.lock_dir / R.LOCK_NAME, os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                seen.update(json.loads(os.pread(fd, 4096, 0)))
            finally:
                os.close(fd)
            raise RuntimeError("壊れた")

        out = io.StringIO()
        with mock.patch.object(R, "_run_job", side_effect=broken), mock.patch.object(R, "_rmtree"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = R.main([str(t), "--head", "pr", "--base", "main", "--canonical", str(t), "--canonical-ref", "main",
                         "--jobs", "1", "--work", str(self.root / "work-broken")])
        self.assertEqual(rc, 1, out.getvalue())
        self.assertEqual(seen.get("repository"), "local/target")
        self.assertEqual(seen.get("head"), _git(t, "rev-parse", "pr"))
        self.assertEqual(seen.get("pid"), os.getpid())
        self.assertLockIsFree()
        self.assertEqual((self.lock_dir / R.LOCK_NAME).read_bytes(), b"")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = R.main([str(t), "--head", "pr", "--base", "main", "--canonical", str(t), "--canonical-ref", "main",
                         "--python", f"{PYVER}={sys.executable}", "--no-wait", "--work", str(self.root / "work-again")])
        self.assertEqual(rc, 0, out.getvalue())
        self.assertLockIsFree()

    def ok_repo(self) -> Path:
        return _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              a:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ok
            """), {})

    def test_the_lock_file_is_only_for_the_user(self):
        """作るときも、 前からあるときも、 この利用者だけが読み書きできる形にする。"""
        t = self.ok_repo()
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lock = self.lock_dir / R.LOCK_NAME
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)
        lock.chmod(0o644)
        p = self.run_it(t)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(lock.stat().st_mode & 0o777, 0o600)

    def test_a_planted_link_is_not_followed(self):
        """ロックの道に置いた symlink・hard link の先のファイルを、 切り詰めない（流さずに止める）。"""
        t = self.ok_repo()
        self.lock_dir.mkdir()
        lock = self.lock_dir / R.LOCK_NAME
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind):
                victim = self.root / f"victim-{kind}"
                victim.write_text("大事な中身")
                if kind == "symlink":
                    lock.symlink_to(victim)
                else:
                    os.link(victim, lock)
                p = self.run_it(t)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("流せません: 同じ利用者の検査を重ねないロックを取れません", p.stderr)
                self.assertNotIn(R.RECORD_PASS, p.stdout)
                self.assertEqual(victim.read_text(), "大事な中身")
                self.assertFalse(self.work.exists())
                lock.unlink()


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

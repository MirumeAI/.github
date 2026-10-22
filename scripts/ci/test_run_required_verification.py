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


def _repo(root: Path, base: dict, change: dict, name: str = "target") -> Path:
    """main に base、 枝 pr に change を commit した Repository。"""
    r = root / name
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    for files, msg in ((base, "base"), (change, "change")):
        if msg == "change":
            _git(r, "switch", "-q", "-c", "pr")
        for rel, text in files.items():
            p = r / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(text), encoding="utf-8")
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "--allow-empty", "-m", msg)
    return r


def _state(repo: Path) -> tuple:
    return (_git(repo, "status", "--porcelain", "--ignored"), _git(repo, "for-each-ref"),
            _git(repo, "rev-parse", "HEAD"), _git(repo, "config", "--list", "--local"))


@unittest.skipIf(R.yaml is None, "PyYAML が要る（CI では先の段で入れる）")
class E2E(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_it(self, target: Path, *extra, canonical: Path | None = None, env: dict | None = None):
        cmd = [sys.executable, str(RUNNER), str(target), "--head", "pr", "--base", "main",
               "--canonical", str(canonical or target), "--canonical-ref", "main",
               "--python", f"{PYVER}={sys.executable}", "--work", str(self.root / "work"), *extra]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              env=dict(os.environ, **(env or {})))

    def wf(self, body: str) -> dict:
        return {".github/workflows/w.yml": body}


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
                    run: touch "$RUNNER_TEMP/../../should-not-exist"
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


class RefusesWhatItCannotRunTest(E2E):
    """**知らない書き方は、 何も流す前に止める**（終了コード 2）。"""

    def _refused(self, body: str, word: str):
        t = _repo(self.root, self.wf(body), {})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn(word, p.stderr)
        self.assertNotIn("job", p.stdout)
        self.assertFalse((self.root / "work").exists(), "止めるときは何も流さない")

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

    def test_unknown_expression(self):
        self._refused("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo "${{ secrets.TOKEN }}"
            """, "secrets.TOKEN")

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


class PathsAndEnvTest(E2E):

    def test_paths_filter_uses_the_changed_files(self):
        body = """\
            on: {pull_request: {paths: ["docs/**", "!docs/skip.md"]}}
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: echo ran
            """
        t = _repo(self.root, self.wf(body), {"docs/skip.md": "x"})
        p = self.run_it(t)
        self.assertEqual(p.returncode, 2, "合う変更が無ければ、 流す workflow が無い")
        self.assertIn("paths に合う変更が無い", p.stdout + p.stderr)
        t2 = _repo(self.root, self.wf(body), {"docs/a/b.md": "x"}, name="target2")
        p2 = self.run_it(t2)
        self.assertEqual(p2.returncode, 0, p2.stdout + p2.stderr)

    def test_home_is_isolated_and_credentials_are_not_passed(self):
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              j:
                runs-on: ubuntu-latest
                steps:
                  - run: |
                      test "$HOME" != "$REAL_HOME_FOR_TEST"
                      test -z "${GH_TOKEN:-}${SSH_AUTH_SOCK:-}${AWS_ACCESS_KEY_ID:-}${CLAUDE_X:-}"
                      touch "$HOME/written-by-pr"
                      echo "FOO=bar" >> "$GITHUB_ENV"
                  - run: test "$FOO" = bar
            """), {})
        real_home = self.root / "real-home"
        real_home.mkdir()
        p = self.run_it(t, env={"HOME": str(real_home), "REAL_HOME_FOR_TEST": str(real_home),
                                "GH_TOKEN": "dummy", "SSH_AUTH_SOCK": "/nowhere",
                                "AWS_ACCESS_KEY_ID": "dummy", "CLAUDE_X": "dummy"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((real_home / "written-by-pr").exists(), "本物の HOME に書かせない")


class ReusableWorkflowTest(E2E):

    def test_reusable_workflow_runs_from_canonical_with_inputs(self):
        canon = _repo(self.root, {
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
                      - if: ${{{{ inputs.flag }}}}
                        run: exit 1
                """}, {}, name="canonical")
        t = _repo(self.root, self.wf("""\
            on: pull_request
            jobs:
              checks:
                uses: MirumeAI/.github/.github/workflows/basic-checks-reusable.yml@main
            """), {"marker.txt": "x"})
        p = self.run_it(t, canonical=canon)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"05 skipped")


class ExprAndGlobTest(unittest.TestCase):

    def test_expressions(self):
        ctx = {"github": {"event_name": "pull_request"}, "inputs": {"flag": True, "s": "Abc"}, "env": {}}
        ex = R.Expr(ctx)
        self.assertTrue(ex.eval("github.event_name != 'push'"))
        self.assertTrue(ex.eval("inputs.s == 'abc'"))          # 文字は大文字小文字を区別しない
        self.assertFalse(ex.eval("!inputs.flag"))
        self.assertTrue(R._cond("${{ inputs.flag }}", ex))
        self.assertFalse(R._cond(None, R.Expr(ctx, "failure")))
        self.assertTrue(R._cond("always()", R.Expr(ctx, "failure")))
        self.assertEqual(R._subst("a-${{ inputs.s }}-${{ inputs.flag }}", ex), "a-Abc-true")
        with self.assertRaises(R.Unsupported):
            ex.eval("secrets.X")
        with self.assertRaises(R.Unsupported):
            ex.eval("hashFiles('x')")

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

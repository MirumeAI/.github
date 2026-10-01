#!/usr/bin/env python3
"""`new_customer_repo.py` が、 手作業で漏れていたものを漏らさないことを確かめる。

**ネットワークを使わない。** `gh` は差し替える。 Organization を叩くと、
権限や状態で結果が変わり、 テストが信用できなくなる（org_audit と同じ）。

確かめること:

  ・作った最初の中身が、 新しい Repository の最初の CI（再利用 workflow と
    同じ検査）を通る。 雛形と正本のどちらかが崩れると、 そこで落ちる
  ・計画の表示と `--check` は書き込まない
  ・Claude Code の中からは `--apply` できない（D5）
  ・main へ直接入れない（Branch を作り、 Pull Request にする）
  ・照合の対象のうち、 顧客案件へ写さないものには理由がある
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import copy_sync_check as C                                # noqa: E402
import new_customer_repo as N                              # noqa: E402
import org_audit as A                                      # noqa: E402

#: 架空の案件。 **実在の顧客名を使わない**（この Repository は Public）。
NAME = "customer-example-part"
REPO = f"/repos/MirumeAI/{NAME}"
PR_LIST = f"{REPO}/pulls?state=all&head=MirumeAI:{N.BRANCH}"
PLAN_LIMIT = ("Upgrade to GitHub Pro or make this repository public to "
              "enable this feature.")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                    "-c", "commit.gpgsign=false", *args],
                   cwd=str(cwd), check=True, capture_output=True)


def _commit_tree(dst: Path) -> None:
    _git(dst, "init", "-q")
    _git(dst, "add", "-A")
    _git(dst, "commit", "-q", "-m", "x")


def _worktree_source(base: Path) -> N.Source:
    """作業ツリーの今の中身を commit した clone。

    **Source は commit から読む。** commit していない変更（このテストを
    書き換えている最中など）も確かめられるように、 写して commit する。
    実行権限も写す（hook は 100755）。
    """
    dst = base / "canonical"
    files = [p for p in (ROOT / N.TEMPLATE).rglob("*") if p.is_file()]
    files += [ROOT / rel for rel in (*N.FROM_CANONICAL, N.SETTINGS)]
    for f in files:
        out = dst / f.relative_to(ROOT)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
    _commit_tree(dst)
    return N.Source(dst)


def _materialize(tree: dict, dst: Path) -> None:
    """最初の中身を、 新しい Repository の clone の形に置く。"""
    for rel, (mode, data) in tree.items():
        p = dst / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if mode == "100755":
            p.chmod(0o755)
    _commit_tree(dst)


_TMP = tempfile.TemporaryDirectory()
SRC = _worktree_source(Path(_TMP.name))
PLAN = N.make_plan(NAME, None, N.PROPERTIES, SRC)


def tearDownModule() -> None:
    _TMP.cleanup()


class _R(SimpleNamespace):
    pass


class FakeGh:
    """`gh api` の代わり。 呼ばれた順に `(method, path, body)` を残す。

    応答は `(method, path)` ごとに `(HTTP の状態, 値)` で持つ。 並べると
    呼ばれるたびに 1 つ進む（最後のものが残る）。 **持っていない呼び出しは
    失敗にする。** 想定外の書き込みを見逃さないため。
    """

    def __init__(self, routes: dict) -> None:
        self.routes = {k: list(v) if isinstance(v, list) else [v]
                       for k, v in routes.items()}
        self.calls = []

    def __call__(self, args: list, data: "bytes | None"):
        assert args[:2] == ["api", "-X"], args
        method, path = args[2], args[3]
        body = json.loads(data.decode("utf-8")) if data else None
        self.calls.append((method, path, body))
        seq = self.routes.get((method, path))
        if not seq:
            return _R(returncode=1, stdout=b"",
                      stderr=f"gh: 想定外の呼び出し {method} {path} (HTTP 599)"
                      .encode())
        status, value = seq.pop(0) if len(seq) > 1 else seq[0]
        if status < 300:
            out = b"" if value is None else json.dumps(value).encode()
            return _R(returncode=0, stdout=out, stderr=b"")
        msg = value if isinstance(value, str) else (
            "Not Found" if status == 404 else "error")
        return _R(returncode=1,
                  stdout=json.dumps({"message": msg}).encode(),
                  stderr=f"gh: {msg} (HTTP {status})".encode())

    def writes(self) -> list:
        return [c for c in self.calls if c[0] != "GET"]


def _schema() -> list:
    """Organization の分類の定義（標準のとおり）。"""
    return [{"property_name": k,
             "allowed_values": list(v) if v else None,
             "default_value": "unclassified" if k == "repo_type" else None}
            for k, v in A.STANDARD.items()]


def _fresh(sha: str = "") -> dict:
    """まだ Repository が無い Organization（読み取り）。"""
    return {
        ("GET", "/user"): (200, {"login": "owner"}),
        ("GET", "/repos/MirumeAI/.github/git/ref/heads/main"):
            (200, {"object": {"sha": sha or SRC.commit()}}),
        ("GET", REPO): (404, None),
        ("GET", "/orgs/MirumeAI/teams/developers"): (200, {"slug": "developers"}),
        ("GET", "/orgs/MirumeAI/teams/maintainers"):
            (200, {"slug": "maintainers"}),
        ("GET", "/orgs/MirumeAI/properties/schema"): (200, _schema()),
    }


def _writes_ok() -> dict:
    """`--apply` の書き込みと、 その途中の読み取りへの応答。"""
    return {
        ("POST", "/orgs/MirumeAI/repos"):
            (201, {"name": NAME, "default_branch": "main"}),
        ("PATCH", f"{REPO}/properties/values"): (204, None),
        ("PATCH", REPO): (200, {}),
        ("PUT", f"/orgs/MirumeAI/teams/developers/repos/MirumeAI/{NAME}"):
            (204, None),
        ("PUT", f"/orgs/MirumeAI/teams/maintainers/repos/MirumeAI/{NAME}"):
            (204, None),
        ("PUT", f"{REPO}/vulnerability-alerts"): (204, None),
        ("GET", f"{REPO}/contents/CLAUDE.md?ref=main"): (404, None),
        ("GET", PR_LIST): (200, []),
        ("GET", f"{REPO}/git/ref/heads/{N.BRANCH}"): (404, None),
        # 作った直後は、 最初の commit がまだ見えないことがある
        ("GET", f"{REPO}/git/ref/heads/main"):
            [(409, "Git Repository is empty."),
             (200, {"object": {"sha": "b" * 40}})],
        ("GET", f"{REPO}/git/commits/" + "b" * 40):
            (200, {"tree": {"sha": "c" * 40}}),
        ("POST", f"{REPO}/git/trees"): (201, {"sha": "d" * 40}),
        ("POST", f"{REPO}/git/commits"): (201, {"sha": "e" * 40}),
        ("POST", f"{REPO}/git/refs"): (201, {}),
        ("POST", f"{REPO}/pulls"):
            (201, {"html_url": f"https://github.com/MirumeAI/{NAME}/pull/1"}),
    }


def _created(**repo_over) -> dict:
    """作って最初の Pull Request を merge した後（`--check` と監査が読む）。"""
    repo = {"name": NAME, "private": True, "archived": False,
            "default_branch": "main", **N.REPO_SETTINGS, **repo_over}
    props = [{"property_name": k, "value": v} for k, v in N.PROPERTIES.items()]
    claude = base64.b64encode(PLAN.tree["CLAUDE.md"][1]).decode()
    r = {
        ("GET", REPO): (200, repo),
        ("GET", f"{REPO}/teams"):
            (200, [{"slug": "developers", "permission": "push"},
                   {"slug": "maintainers", "permission": "admin"}]),
        ("GET", f"{REPO}/vulnerability-alerts"): (204, None),
        ("GET", f"{REPO}/git/trees/main?recursive=1"):
            (200, {"tree": [{"path": p, "type": "blob"} for p in PLAN.tree]}),
        ("GET", f"{REPO}/properties/values"): (200, props),
        # ここから Organization 監査（org_audit.py）が読むもの
        ("GET", "/orgs/MirumeAI"): (200, {"plan": {"name": "free"}}),
        ("GET", "/orgs/MirumeAI/properties/schema"): (200, _schema()),
        ("GET", "/orgs/MirumeAI/issue-types"):
            (200, [{"name": t, "is_enabled": True}
                   for t in ("Task", "Bug", "Feature", "Experiment")]),
        ("GET", "/orgs/MirumeAI/issue-fields"): (200, []),
        ("GET", "/orgs/MirumeAI/teams"):
            (200, [{"slug": "developers"}, {"slug": "maintainers"}]),
        ("GET", "/orgs/MirumeAI/teams/developers/repos"): (200, [{"name": NAME}]),
        ("GET", "/orgs/MirumeAI/teams/maintainers/repos"):
            (200, [{"name": NAME}]),
        ("GET", "/orgs/MirumeAI/repos?per_page=100"): (200, [repo]),
        ("GET", "/orgs/MirumeAI/properties/values"):
            (200, [{"repository_name": NAME, "properties": props}]),
        ("GET", f"{REPO}/branches/main/protection"): (403, PLAN_LIMIT),
        ("GET", f"{REPO}/contents/CLAUDE.md?ref=main"):
            (200, {"content": claude}),
    }
    for f in A.REQUIRED_FILES:
        r[("GET", f"{REPO}/contents/{f}")] = (200, {"content": ""})
    return r


def _main(args: list, fake: FakeGh, env: "dict | None" = None) -> tuple:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = N.main(args, env=env or {}, api=N.Api(fake), src=SRC,
                      sleep=lambda s: None)
    return code, out.getvalue() + err.getvalue()


class NameTest(unittest.TestCase):

    def test_the_form_of_the_name(self) -> None:
        for good in ("customer-example-part", "customer-a1-b2-c3"):
            N.check_name(good)
        for bad in ("example-part", "customer-example", "Customer-example-part",
                    "customer-example_part", "customer-example-part-",
                    "customer--part", "customer-例-part"):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                N.check_name(bad)

    def test_the_title_is_made_from_the_name(self) -> None:
        """既存の案件と同じ付け方（Repository の名前から作る）。"""
        self.assertEqual(N.default_title(NAME), "example part")
        self.assertEqual(PLAN.title, "example part")


class TheFirstContentTest(unittest.TestCase):
    """最初の中身に、 手で写していたものが漏れなく入ること。"""

    def test_every_template_file_is_there(self) -> None:
        tpl = {p.relative_to(ROOT / N.TEMPLATE).as_posix()
               for p in (ROOT / N.TEMPLATE).rglob("*") if p.is_file()}
        self.assertIn("CLAUDE.md", tpl)
        self.assertLessEqual(tpl, set(PLAN.tree))

    def test_the_guard_comes_from_the_canonical(self) -> None:
        """**hook と command は正本と 1 バイトも違わない。**

        Plan 制約で branch protection が使えない Private な Repository では、
        hook が main を守る唯一の機械的な壁になる。 雛形に無かったので、
        手で写さないと壁が無いまま始まっていた。
        """
        for rel in N.FROM_CANONICAL:
            with self.subTest(file=rel):
                self.assertEqual(PLAN.tree[rel][1], (ROOT / rel).read_bytes())
        self.assertEqual(PLAN.tree[".claude/hooks/guard-main.sh"][0], "100755")

    def test_they_are_not_copied_into_the_template(self) -> None:
        """**雛形に複製を置かない。** 正本と雛形の 2 つにすると、 片方だけ直る。"""
        for rel in (*N.FROM_CANONICAL, N.SETTINGS):
            with self.subTest(file=rel):
                self.assertFalse((ROOT / N.TEMPLATE / rel).exists())

    def test_the_name_is_filled_in(self) -> None:
        for rel in ("README.md", "CLAUDE.md"):
            self.assertIn("example part", PLAN.tree[rel][1].decode("utf-8"))
        for rel, (_, data) in PLAN.tree.items():
            with self.subTest(file=rel):
                self.assertNotIn(N.PLACEHOLDER.encode("utf-8"), data)

    def test_settings_add_only_the_license_rule(self) -> None:
        """`.claude/settings.json` は、 正本に `*.lic` の拒否を足しただけ。

        期待値は script の定数から取らない（取ると、 定数を空にしても通る）。
        """
        canon = json.loads((ROOT / N.SETTINGS).read_text(encoding="utf-8"))
        made = json.loads(PLAN.tree[N.SETTINGS][1].decode("utf-8"))
        self.assertEqual(made["permissions"]["deny"],
                         canon["permissions"]["deny"]
                         + ["Read(**/*.lic)", "Edit(**/*.lic)"])
        made["permissions"]["deny"] = canon["permissions"]["deny"]
        self.assertEqual(made, canon, "拒否の追加のほかにも違いがある")

    def test_the_template_points_here(self) -> None:
        """雛形の README は、 作り方としてこの script を示す。"""
        readme = (ROOT / N.TEMPLATE / "README.md").read_text(encoding="utf-8")
        self.assertIn("scripts/ci/new_customer_repo.py", readme)


class ItPassesTheFirstCITest(unittest.TestCase):
    """**作った中身が、 新しい Repository の最初の CI を通ること。**

    検査の一覧は再利用 workflow から読む。 ここに一覧を書くと、 workflow に
    検査を足しても、 ここでは確かめないまま合格になる（`test_copy_sync.py`
    の見本で実際に起きた）。
    """

    def test_every_step_of_the_reusable_workflow_passes(self) -> None:
        wf = (ROOT / ".github/workflows/basic-checks-reusable.yml"
              ).read_text(encoding="utf-8")
        steps = re.findall(r"^\s+run: (python|bash) validators/(scripts/ci/\S+)\s*$",
                           wf, re.M)
        self.assertGreaterEqual(len(steps), 5, steps)
        with tempfile.TemporaryDirectory() as td:
            tgt = Path(td) / "target"
            _materialize(PLAN.tree, tgt)
            env = dict(os.environ, CI_ROOT=str(tgt), CANONICAL_ROOT=str(ROOT))
            for prog, script in steps:
                with self.subTest(script=script):
                    r = subprocess.run(
                        [sys.executable if prog == "python" else "bash",
                         str(ROOT / script)],
                        env=env, cwd=str(tgt), capture_output=True, text=True)
                    out = r.stdout + r.stderr
                    self.assertEqual(r.returncode, 0, out[-1200:])
                    if script.endswith("copy_sync_check.py"):
                        # 照合を飛ばして合格、 になっていないこと
                        self.assertIn(f"照合: {len(N.FROM_CANONICAL)} ファイル",
                                      out)


class EverySharedFileIsDecidedTest(unittest.TestCase):

    def test_copied_files_are_compared(self) -> None:
        """写すものはすべて照合の対象（写した後のずれを CI が見つける）。"""
        self.assertLessEqual(set(N.FROM_CANONICAL), set(C.SHARED))

    def test_the_rest_has_a_reason(self) -> None:
        """**照合の対象を足したら、 顧客案件へ写すかを決めること。**

        決めないまま足すと、 新しい案件にだけ入らず、 照合でも見つからない
        （持っていないファイルは照合しない）。
        """
        self.assertEqual(set(C.SHARED) - set(N.FROM_CANONICAL),
                         set(N.NOT_FOR_CUSTOMER))
        for rel, why in N.NOT_FOR_CUSTOMER.items():
            with self.subTest(file=rel):
                self.assertTrue(why.strip())


class TheRulesAreWrittenOutTest(unittest.TestCase):
    """**運用の決まりを、 script の定数とは別にここへ書く。**

    script の定数どうしを比べるだけでは、 定数を書き換えても通ってしまう。
    """

    def test_squash_only_and_delete_the_branch(self) -> None:
        """Merge は Squash and merge だけ（CLAUDE.md）。 merge 後に Branch を消す。"""
        for key, value in (("allow_squash_merge", True),
                           ("allow_merge_commit", False),
                           ("allow_rebase_merge", False),
                           ("delete_branch_on_merge", True)):
            with self.subTest(key=key):
                self.assertIs(N.REPO_SETTINGS[key], value)

    def test_the_teams(self) -> None:
        """Team は 2 つ（development-docs の D12）。"""
        self.assertEqual(dict(N.TEAMS),
                         {"developers": "push", "maintainers": "admin"})

    def test_before_going_live_it_is_not_production(self) -> None:
        """作った時点では稼働前（現場で稼働したら人が変える）。"""
        self.assertEqual((N.PROPERTIES["repo_type"],
                          N.PROPERTIES["data_classification"],
                          N.PROPERTIES["production_impact"],
                          N.PROPERTIES["governance_profile"]),
                         ("customer-project", "customer-confidential",
                          "none", "standard"))


class TheClassificationPassesTheAuditTest(unittest.TestCase):
    """分類の値と組が、 監査の規則に合うこと。 判定は org_audit そのもの。"""

    def _findings(self, props: dict) -> list:
        repo = {"private": True, "archived": False, "default_branch": "main",
                "properties": props,
                "files": {f: True for f in A.REQUIRED_FILES},
                "protection": None, "required_checks": None,
                "plan_limited": []}
        org = {"plan": "free",
               "properties": {k: {"required": False, "values": list(v or ())}
                              for k, v in A.STANDARD.items()},
               "issue_types": None, "issue_fields": [],
               "teams": {s: [] for s, _ in N.TEAMS}}
        findings, _ = A.classify(org, {NAME: repo}, {})
        return [f["code"] for f in findings if f["repo"] == NAME]

    def test_before_going_live(self) -> None:
        self.assertEqual(self._findings(dict(N.PROPERTIES)), [])

    def test_after_going_live(self) -> None:
        """雛形の README の手順 3 のとおりに変えれば、 所見は出ない。"""
        self.assertEqual(self._findings(dict(
            N.PROPERTIES, production_impact="direct",
            governance_profile="strict")), [])

    def test_changing_only_one_is_found(self) -> None:
        """片方だけ変えると監査が見つける（手順 3 で両方を変える理由）。"""
        self.assertIn("profile_mismatch", self._findings(dict(
            N.PROPERTIES, production_impact="direct")))


class ThePlanChangesNothingTest(unittest.TestCase):

    def test_it_only_reads(self) -> None:
        fake = FakeGh(_fresh())
        code, out = _main([NAME], fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn("まだ何も変えていません", out)
        for word in ("Repository を作る", "分類", "Squash", "Team",
                     "Dependabot", "Pull Request", N.SETTINGS):
            self.assertIn(word, out)

    def test_an_old_clone_is_stopped(self) -> None:
        """古い clone から写すと、 最初の Pull Request の照合で必ず落ちる。"""
        code, out = _main([NAME], FakeGh(_fresh(sha="0" * 40)))
        self.assertEqual(code, 1, out)
        self.assertIn("git pull", out)

    def test_what_cannot_be_read_is_not_ok(self) -> None:
        """**確かめられなかったものを OK にしない。**"""
        routes = _fresh()
        del routes[("GET", "/orgs/MirumeAI/properties/schema")]
        code, out = _main([NAME], FakeGh(routes))
        self.assertEqual(code, 1, out)
        self.assertIn("不明", out)


class ApplyTest(unittest.TestCase):

    def _apply(self, routes: dict) -> tuple:
        fake = FakeGh(routes)
        code, out = _main([NAME, "--apply"], fake)
        return code, out, fake

    def test_it_does_everything_in_order(self) -> None:
        code, out, fake = self._apply({**_fresh(), **_writes_ok()})
        self.assertEqual(code, 0, out)
        w = fake.writes()
        self.assertEqual([(m, p) for m, p, _ in w], [
            ("POST", "/orgs/MirumeAI/repos"),
            ("PATCH", f"{REPO}/properties/values"),
            ("PATCH", REPO),
            ("PUT", f"/orgs/MirumeAI/teams/developers/repos/MirumeAI/{NAME}"),
            ("PUT", f"/orgs/MirumeAI/teams/maintainers/repos/MirumeAI/{NAME}"),
            ("PUT", f"{REPO}/vulnerability-alerts"),
            ("POST", f"{REPO}/git/trees"),
            ("POST", f"{REPO}/git/commits"),
            ("POST", f"{REPO}/git/refs"),
            ("POST", f"{REPO}/pulls"),
        ])
        create = w[0][2]
        self.assertIs(create["private"], True)
        self.assertIs(create["auto_init"], True)
        self.assertEqual({p["property_name"]: p["value"]
                          for p in w[1][2]["properties"]}, N.PROPERTIES)
        self.assertEqual(w[2][2], N.REPO_SETTINGS)
        self.assertEqual([w[3][2], w[4][2]],
                         [{"permission": "push"}, {"permission": "admin"}])
        tree = w[6][2]
        self.assertEqual(tree["base_tree"], "c" * 40)
        self.assertEqual({e["path"] for e in tree["tree"]}, set(PLAN.tree))
        self.assertEqual(w[7][2]["parents"], ["b" * 40])
        self.assertIn(f"{NAME}/pull/1", out)
        self.assertIn("--check", out)

    def test_it_never_writes_main(self) -> None:
        """**main へ直接入れない。** Branch を作り、 Pull Request にする。"""
        code, out, fake = self._apply({**_fresh(), **_writes_ok()})
        self.assertEqual(code, 0, out)
        w = fake.writes()
        self.assertEqual(w[8][2]["ref"], f"refs/heads/{N.BRANCH}")
        self.assertNotEqual(N.BRANCH, "main")
        self.assertEqual((w[9][2]["base"], w[9][2]["head"]), ("main", N.BRANCH))
        for method, path, _ in w:
            self.assertNotIn("/git/refs/heads/", path, (method, path))
            self.assertNotIn("/contents/", path, (method, path))

    def test_a_problem_found_first_changes_nothing(self) -> None:
        """確かめた時点で NG があれば、 何も変えずに止める。"""
        routes = {**_fresh(), **_writes_ok()}
        routes[("GET", "/orgs/MirumeAI/teams/maintainers")] = (404, None)
        code, out, fake = self._apply(routes)
        self.assertEqual(code, 1, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn("何も変えずに止めました", out)

    def test_a_failed_step_tells_how_to_go_on(self) -> None:
        """途中で止まったら、 そこで止め、 続きから行う方法を出す。"""
        routes = {**_fresh(), **_writes_ok()}
        team = f"/orgs/MirumeAI/teams/developers/repos/MirumeAI/{NAME}"
        routes[("PUT", team)] = (403, "Must have admin rights to Repository.")
        code, out, fake = self._apply(routes)
        self.assertEqual(code, 1, out)
        self.assertEqual(fake.writes()[-1][1], team, "止まった後も書き込んだ")
        self.assertIn("--apply --resume", out)
        self.assertIn("Must have admin rights", out)

    def test_the_body_follows_the_pull_request_template(self) -> None:
        tpl = (ROOT / ".github/PULL_REQUEST_TEMPLATE.md").read_text(
            encoding="utf-8")
        self.assertEqual(re.findall(r"^## .+$", N.pr_body(PLAN), re.M),
                         re.findall(r"^## .+$", tpl, re.M))


class TheAgentCannotApplyTest(unittest.TestCase):
    """**Claude Code の中からは `--apply` できない**（D5。 設定は人が変える）。

    hook は `gh api` の書き込みを止めるが、 この script の中で呼ぶ `gh` は
    見えない。 ここで止めないと、 hook を素通りして設定を変えられる。
    """

    def test_apply_is_refused_before_any_call(self) -> None:
        fake = FakeGh({**_fresh(), **_writes_ok()})
        code, out = _main([NAME, "--apply"], fake, env={"CLAUDECODE": "1"})
        self.assertEqual(code, 2, out)
        self.assertEqual(fake.calls, [], "断る前に API を呼んでいる")
        self.assertIn("Claude Code", out)

    def test_the_plan_is_allowed(self) -> None:
        code, out = _main([NAME], FakeGh(_fresh()), env={"CLAUDECODE": "1"})
        self.assertEqual(code, 0, out)


class ResumeTest(unittest.TestCase):

    def test_an_existing_repository_needs_resume(self) -> None:
        routes = {**_fresh(), **_writes_ok()}
        routes[("GET", REPO)] = (200, {"name": NAME, "default_branch": "main"})
        fake = FakeGh(routes)
        code, out = _main([NAME, "--apply"], fake)
        self.assertEqual(code, 1, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn("--resume", out)

    def test_resume_skips_what_is_done_and_keeps_values(self) -> None:
        """**入っている値を上書きしない。** 人が直した値を黙って戻さない。"""
        routes = {**_fresh(), **_writes_ok()}
        routes[("GET", REPO)] = (200, {"name": NAME, "default_branch": "main"})
        routes[("GET", f"{REPO}/properties/values")] = (200, [
            {"property_name": "repo_type", "value": "unclassified"},  # 既定値
            {"property_name": "lifecycle", "value": "active"},        # 同じ
            {"property_name": "criticality", "value": "medium"},      # 人が直した
        ])
        routes[("GET", PR_LIST)] = (200, [
            {"state": "open", "merged_at": None,
             "html_url": f"https://github.com/MirumeAI/{NAME}/pull/1"}])
        fake = FakeGh(routes)
        code, out = _main([NAME, "--apply", "--resume"], fake)
        self.assertEqual(code, 0, out)
        w = fake.writes()
        self.assertNotIn(("POST", "/orgs/MirumeAI/repos"),
                         [(m, p) for m, p, _ in w])
        props = {p["property_name"]: p["value"]
                 for m, p_, b in w if p_.endswith("/properties/values")
                 for p in b["properties"]}
        self.assertEqual(props["repo_type"], "customer-project")
        self.assertNotIn("lifecycle", props)
        self.assertNotIn("criticality", props)
        self.assertIn("criticality=medium", out)
        self.assertFalse([p for m, p, _ in w if m == "POST"],
                         "Pull Request があるのに作り直した")

    def test_resume_needs_apply(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            N.main([NAME, "--resume"], env={}, api=N.Api(FakeGh({})), src=SRC)


class CheckTest(unittest.TestCase):

    @staticmethod
    def _check(routes: dict) -> tuple:
        """監査は省く（監査そのものは test_a_good_repository で通す）。"""
        fake = FakeGh(routes)
        out = io.StringIO()
        with redirect_stdout(out):
            code = N.check(N.Api(fake), PLAN,
                           audit=lambda api, name: ([], [], [], True))
        return code, out.getvalue(), fake

    def test_a_good_repository_passes_reading_only(self) -> None:
        """監査まで通して、 書き込みが 1 つも無いこと。"""
        fake = FakeGh(_created())
        code, out = _main([NAME, "--check"], fake)
        self.assertEqual(code, 0, out)
        self.assertEqual(fake.writes(), [])
        self.assertIn("Organization 監査の所見なし", out)
        self.assertIn("問題ありません", out)

    def test_before_the_merge(self) -> None:
        """最初の Pull Request を merge する前は、 そう分かること。"""
        routes = _created()
        routes[("GET", f"{REPO}/git/trees/main?recursive=1")] = (
            200, {"tree": [{"path": "README.md", "type": "blob"}]})
        for f in A.REQUIRED_FILES:
            routes[("GET", f"{REPO}/contents/{f}")] = (404, None)
        code, out = _main([NAME, "--check"], FakeGh(routes))
        self.assertEqual(code, 1, out)
        self.assertIn("まだ merge されていない", out)
        self.assertIn("CLAUDE.md が無い", out)            # 監査も見つける

    def test_a_merge_commit_is_found(self) -> None:
        code, out, _ = self._check(_created(allow_merge_commit=True))
        self.assertEqual(code, 1, out)
        self.assertIn("allow_merge_commit=True", out)

    def test_dependabot_off_is_found(self) -> None:
        routes = _created()
        routes[("GET", f"{REPO}/vulnerability-alerts")] = (404, None)
        code, out, _ = self._check(routes)
        self.assertEqual(code, 1, out)
        self.assertIn("Dependabot Alerts が無効", out)

    def test_a_missing_team_is_found(self) -> None:
        routes = _created()
        routes[("GET", f"{REPO}/teams")] = (
            200, [{"slug": "maintainers", "permission": "admin"}])
        code, out, _ = self._check(routes)
        self.assertEqual(code, 1, out)
        self.assertIn("developers=なし", out)

    def test_a_changed_classification_is_only_a_note(self) -> None:
        """稼働した後に変えた分類は NG にしない（正しさは監査が見る）。"""
        routes = _created()
        routes[("GET", f"{REPO}/properties/values")] = (200, [
            {"property_name": k, "value": v} for k, v in dict(
                N.PROPERTIES, production_impact="direct",
                governance_profile="strict").items()])
        code, out, _ = self._check(routes)
        self.assertEqual(code, 0, out)
        self.assertIn("production_impact=direct", out)


class ItIsWiredIntoCITest(unittest.TestCase):
    """**呼ばれていなければ意味が無い。**"""

    def test_the_workflow_runs_this_test(self) -> None:
        wf = (ROOT / ".github/workflows/basic-checks.yml").read_text(
            encoding="utf-8")
        self.assertIn("scripts/ci/test_new_customer_repo.py", wf)


if __name__ == "__main__":
    unittest.main()

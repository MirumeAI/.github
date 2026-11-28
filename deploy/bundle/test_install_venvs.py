"""据え付けた機体の venv を束から入れる道具（install_venvs.py）と、製品の束を作る道具（make_product_bundle.py）の試験。
偽の案件（fake-case）の束と versions.yaml と pyproject.toml で照らし、pip は流さない（命令を記録する偽物で照らす）。製品の
束を作る試験は、偽の Git の Repository で本物の git を流し、wheel を作る所だけを偽物にする。案件に依らない共通の試験
（正本は MirumeAI/.github の同じ道。MirumeAI/.github#237）。この Repository の物を照らす試験は、案件の Repository でだけ流す
（正本では、雛形から作った最初の中身を、新しい案件を作る道具の試験が照らす）。"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
#: この Repository が案件の Repository か（根に versions.yaml と pyproject.toml がある。正本〔MirumeAI/.github〕には無い）
IN_CASE = (REPO / "versions.yaml").is_file() and (REPO / "pyproject.toml").is_file()
NOT_IN_CASE = "案件の Repository でない（正本では、新しい案件を作る道具の試験が雛形から作った中身を照らす）"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


IV = _load("install_venvs")
PB = _load("make_product_bundle")

PDM_COMMIT, IA_COMMIT = "a" * 40, "b" * 40
#: 偽の案件の製品のパッケージが PDM の差し込みの群に登録する名前（群ごと）
FAKE_PARTS = {"mirumeai_pdm.extra_checks": ["check_a"], "mirumeai_pdm.extract_methods": ["method_a", "method_b"]}
#: 偽の案件の根の pyproject.toml（製品の名前・extra・部品の登録）
FAKE_PYPROJECT = """\
[build-system]
requires = ["setuptools==84.2.1", "wheel==0.48.0"]
build-backend = "setuptools.build_meta"

[project]
name = "fake-case"
dynamic = ["version"]
dependencies = ["PyYAML>=6.0,<7"]

[project.optional-dependencies]
parts = [
  "numpy==2.2.6",   # 判定の venv の lock の版
]

[project.scripts]
fake-case = "fake_case.cli:main"

[project.entry-points."mirumeai_pdm.extra_checks"]
check_a = "fake_case.parts.checks"

[project.entry-points."mirumeai_pdm.extract_methods"]
method_b = "fake_case.parts.methods:B"
method_a = "fake_case.parts.methods:A"

[tool.setuptools.dynamic]
version = {attr = "fake_case.__version__"}
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_bundle(root: Path, wheels: "dict[str, bytes]", main: str, extra: "dict[str, str]",
                files: "dict[str, str] | None" = None) -> "tuple[str, str]":
    """偽の束（wheelhouse/・根のファイル・SHA256SUMS・release.json）。 (SHA256SUMS の sha256, 本体の wheel の sha256)。"""
    (root / "wheelhouse").mkdir(parents=True)
    lines = []
    for name, data in wheels.items():
        (root / "wheelhouse" / name).write_bytes(data)
        lines.append(f"{sha(data)}  wheelhouse/{name}")
    for name, text in (files or {}).items():
        (root / name).write_text(text, encoding="utf-8")
        lines.append(f"{sha(text.encode())}  {name}")
    (root / "SHA256SUMS").write_text("\n".join(sorted(lines, key=lambda l: l.split("  ")[1])) + "\n", encoding="utf-8")
    sums = sha((root / "SHA256SUMS").read_bytes())
    release = dict(extra, wheel=main, wheel_sha256=sha(wheels[main]), sha256sums_sha256=sums)
    (root / "release.json").write_text(json.dumps(release), encoding="utf-8")
    return sums, sha(wheels[main])


class FakeRun:
    """命令を流さずに記録する（venv を作る命令はフォルダを作る。照らしの命令は合う結果を返す）。 fail_install を渡すと、
    その回目の pip install で落ちる。"""

    def __init__(self, log=None, verify_out="[]", fail_install=None, files_out="[]"):
        self.calls, self.envs, self.verify_out, self.fail_install = [], [], verify_out, fail_install
        self.files_out = files_out

    def __call__(self, cmd, capture=False, env=None):
        self.calls.append([str(c) for c in cmd])
        self.envs.append(env)
        if "install" in cmd and self.fail_install is not None:
            self.fail_install -= 1
            if self.fail_install == 0:
                raise IV.Stop("落ちました: pip install …（偽物）")
        if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv":
            Path(cmd[-1], "bin").mkdir(parents=True, exist_ok=True)
        if IV._VERIFY in cmd:
            return self.verify_out
        if IV._FILES in cmd:
            return self.files_out
        return ""


def umask_now() -> int:
    m = os.umask(0)
    os.umask(m)
    return m


class InstallVenvs(unittest.TestCase):

    def setUp(self):
        # 試験の土台の権限を、流す機械の umask（開発サーバは 0002）によらない形にする（道具はグループとほかの人が書ける物を
        # 断る）。終わりに元へ戻す
        self.addCleanup(os.umask, os.umask(0o022))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.root, self.repo = self.tmp / "case", self.tmp / "case" / "repo"
        self.wh = self.root / "wheelhouse"
        (self.repo / "machines").mkdir(parents=True)
        (self.repo / "release").mkdir()
        self.pdm_sums, self.pdm_wheel = make_bundle(
            self.wh / "pdm" / "v2.0.0",
            {"mirumeai_pdm-2.0.0-cp310-cp310-linux_x86_64.whl": b"pdm", "numpy-2.2.6-cp310-cp310-linux_x86_64.whl": b"np",
             "torch-2.10.0+cu128-cp310-cp310-linux_x86_64.whl": b"torch"},
            "mirumeai_pdm-2.0.0-cp310-cp310-linux_x86_64.whl",
            {"product": "pdm", "version": "2.0.0", "tag": "v2.0.0", "commit": PDM_COMMIT})
        lock = "fastapi==0.128.0 \\\n    --hash=sha256:" + "1" * 64 + "\n"
        self.ia_sums, self.ia_wheel = make_bundle(
            self.wh / "ia" / "v1.0.0",
            {"mirumeai_image_acquisition-1.0.0-py3-none-any.whl": b"ia", "fastapi-0.128.0-py3-none-any.whl": b"fa"},
            "mirumeai_image_acquisition-1.0.0-py3-none-any.whl",
            {"product": "image-acquisition-pdm", "version": "1.0.0", "tag": "v1.0.0", "commit": IA_COMMIT,
             "pdm": {"version": "2.0.0", "tag": "v2.0.0", "sha256sums_sha256": self.pdm_sums}},
            files={"requirements-web.lock": lock, "requirements-guard.lock": lock})
        _, self.product_wheel = make_bundle(
            self.wh / "fake-case" / "0.1.0", {"fake_case-0.1.0-py3-none-any.whl": b"product"},
            "fake_case-0.1.0-py3-none-any.whl", {"product": "fake-case", "version": "0.1.0", "commit": "c" * 40})
        self.write_versions()
        (self.repo / "pyproject.toml").write_text(FAKE_PYPROJECT, encoding="utf-8")
        self.machine = self.repo / "machines" / "M01.yaml"
        self.machine.write_text("schema_version: 1\nmachine:\n  symbol: M01\n  kind: installed\n", encoding="utf-8")
        self.release = self.repo / "release" / "R1.yaml"
        self.release.write_text("product_package:\n  name: fake-case\n  version: \"0.1.0\"\n"
                                f"  wheel_sha256: \"{self.product_wheel}\"\n", encoding="utf-8")

    def write_versions(self, **over):
        v = {"ia_sums": self.ia_sums, "ia_wheel": self.ia_wheel, "pdm_sums": self.pdm_sums, "pdm_wheel": self.pdm_wheel}
        v.update(over)
        text = ("schema_version: 2\n"
                "image_acquisition_pdm:\n  repository: MirumeAI/Image-Acquisition-PDM\n  tag: v1.0.0\n"
                f"  commit: {IA_COMMIT}\n")
        if v["ia_sums"] is not None:
            text += f"  sha256sums_sha256: {v['ia_sums']}\n"
        text += f"  wheel_sha256: {v['ia_wheel']}\n"
        text += (f"pdm:\n  repository: MirumeAI/PDM\n  tag: v2.0.0\n  commit: {PDM_COMMIT}\n"
                 f"  sha256sums_sha256: {v['pdm_sums']}\n  wheel_sha256: {v['pdm_wheel']}\n")
        (self.repo / "versions.yaml").write_text(text, encoding="utf-8")

    def replace_ia_file(self, name, text):
        """撮像側の束の根のファイルを替え、 SHA256SUMS と versions.yaml の hash を合わせ直す（ほかの照らしだけを試す）。"""
        bundle = self.wh / "ia" / "v1.0.0"
        (bundle / name).write_text(text, encoding="utf-8")
        sums = bundle / "SHA256SUMS"
        lines = [l for l in sums.read_text(encoding="utf-8").splitlines() if not l.endswith("  " + name)]
        lines.append(f"{sha(text.encode())}  {name}")
        sums.write_text("\n".join(sorted(lines, key=lambda l: l.split("  ")[1])) + "\n", encoding="utf-8")
        self.write_versions(ia_sums=sha(sums.read_bytes()))

    def set_ia_release(self, **over):
        rel = self.wh / "ia" / "v1.0.0" / "release.json"
        data = json.loads(rel.read_text(encoding="utf-8"))
        data.update(over)
        rel.write_text(json.dumps(data), encoding="utf-8")

    def main(self, *args, run=FakeRun):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        made = []

        def make_runner(log):
            r = run(log)
            made.append(r)
            return r
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch.object(IV, "python_version", return_value=(3, 10, 20)):
            rc = IV.main(["--machine", str(self.machine), "--system-release", str(self.release),
                          "--root", str(self.root), "--python", "python3.10", *args], repo=self.repo,
                         make_runner=make_runner)
        return rc, out.getvalue(), err.getvalue(), made

    def refuses(self, says, *args):
        rc, out, err, made = self.main(*args)
        self.assertEqual(rc, 1, out + err)
        self.assertIn(says, err)
        self.assertFalse((self.root / ".venvs").exists(), "止めたのに venv を作った")
        return err

    # ---- 照らしと入れ方

    def test_installs_both_venvs_without_an_index_and_with_hashes(self):
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        calls = made[-1].calls
        installs = [c for c in calls if "install" in c]
        self.assertEqual(len(installs), 7)                       # Web 3・判定 4
        for c in installs:
            self.assertEqual(c[1:4], ["-I", "-m", "pip"])
            self.assertIn("--no-index", c)
            self.assertIn("--require-hashes", c)
            self.assertNotIn("--index-url", c)
            self.assertNotIn("--extra-index-url", c)
        for env in made[-1].envs:
            if env is not None:
                self.assertFalse([k for k in env if k.startswith("PIP_") and k not in (
                    "PIP_CONFIG_FILE", "PIP_NO_INPUT", "PIP_DISABLE_PIP_VERSION_CHECK")])
                self.assertEqual(env["PIP_CONFIG_FILE"], os.devnull)
        for name in ("web", "ai"):
            mark = json.loads((self.root / ".venvs" / name / IV.MARK).read_text(encoding="utf-8"))
            self.assertTrue(mark["complete"])
            self.assertEqual(mark["kind"], "installed")
        # 判定の venv: PDM の束の wheel ぜんぶを hash つきで・撮像側と製品は依存なし
        req = (self.root / ".cache" / "install_venvs" / "req-ai" / "pdm.txt").read_text(encoding="utf-8")
        self.assertIn("torch==2.10.0+cu128 --hash=sha256:", req)
        self.assertEqual(len(req.splitlines()), 3)
        product = [c for c in installs if c[-1].endswith("product.txt")]
        self.assertTrue(all("--no-deps" in c for c in product))

    def test_index_settings_in_the_environment_are_not_passed(self):
        with mock.patch.dict(os.environ, {"PIP_INDEX_URL": "http://example.invalid/simple",
                                          "PIP_EXTRA_INDEX_URL": "http://example.invalid/x",
                                          "PYTHONPATH": str(self.tmp / "elsewhere"), "PYTHONHOME": str(self.tmp),
                                          "_PIP_STANDALONE_CERT": str(self.tmp / "ca.pem")}):
            rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        pip_envs = [e for c, e in zip(made[-1].calls, made[-1].envs) if c[2:4] == ["-m", "pip"]]
        self.assertTrue(pip_envs)
        for env in pip_envs:
            self.assertIsNotNone(env)
            self.assertNotIn("PIP_INDEX_URL", env)
            self.assertNotIn("PIP_EXTRA_INDEX_URL", env)
            self.assertNotIn("PYTHONPATH", env)            # PYTHONPATH の上の物を「入っている」と見なさせない
            self.assertNotIn("PYTHONHOME", env)
            self.assertNotIn("_PIP_STANDALONE_CERT", env)     # 信じる CA を pip の内部の名前から変えさせない
        # venv を作る命令も -I で流し、絞った環境を渡す
        venv_runs = [(c, e) for c, e in zip(made[-1].calls, made[-1].envs) if "venv" in c[:4]]
        self.assertEqual(len(venv_runs), 2)
        for c, env in venv_runs:
            self.assertEqual(c[1:4], ["-I", "-m", "venv"], c)
            self.assertIsNotNone(env, c)
            self.assertFalse([k for k in env if k.startswith(("PIP_INDEX", "PIP_EXTRA", "PYTHON", "_PIP_"))], c)

    def test_a_dev_machine_is_refused(self):
        self.machine.write_text("machine:\n  symbol: M01\n  kind: dev\n", encoding="utf-8")
        self.refuses("開発の機体（kind: dev）では使いません")

    def test_the_symbol_must_be_the_file_name(self):
        self.machine.write_text("machine:\n  symbol: M02\n  kind: shown\n", encoding="utf-8")
        self.refuses("machine.symbol")

    def test_versions_without_the_bundle_hash_are_refused(self):
        self.write_versions(ia_sums=None)
        self.refuses("sha256sums_sha256 がありません")

    def test_versions_written_so_that_yaml_reads_another_value_are_refused(self):
        """YAML では同じ鍵・別の深さになる書き方（引用符・`:` の前の空白・`?`・タグ・アンカー・エスケープ・違う字下げ）で
        書いた versions.yaml は、照らす値と YAML の読み手が読む値が食い違いうるので止める。"""
        text = (self.repo / "versions.yaml").read_text(encoding="utf-8")
        line = f"  commit: {PDM_COMMIT}\n"
        other = "b" * 40
        keys = (text + f'"pdm":\n  commit: {other}\n', text + f"'pdm':\n  commit: {other}\n",
                text + f"pdm :\n  commit: {other}\n", text + f"? pdm\n:\n  commit: {other}\n",
                text + f"!!str pdm:\n  commit: {other}\n", text + f"&a pdm:\n  commit: {other}\n",
                text + f'"p\\x64m":\n  commit: {other}\n', text + f"---\npdm:\n  commit: {other}\n",
                text.replace(line, line + f'  "commit": {other}\n'), text.replace(line, line + f"  commit : {other}\n"),
                text.replace(line, line + f"  !!str commit: {other}\n"), text.replace(line, line + f"  &b commit: {other}\n"),
                text.replace(line, line + "  <<: *x\n"), text.replace(line, line + f"  - commit: {other}\n"),
                text + f"\xa0pdm:\n  commit: {other}\n", text.replace(line, line + f"  \xa0commit: {other}\n"))
        indents = (text.replace(line, line + f"   commit: {other}\n"), text.replace(line, line + f" commit: {other}\n"),
                   text.replace(line, line + f"  extra:\n    commit: {other}\n"))
        # 値の側で流れの形・複数行の引用符を始め、YAML では後ろの行を文字列の中に隠すか、同じ節の続きにする。
        # `:` の後に空白の無い値も、YAML では鍵にならない
        values = (text.replace("pdm:\n  repository: MirumeAI/PDM\n", 'pdm: {repository: "x\n')
                  .replace(line, line + f'  repository: y", commit: {other}}}\n'),
                  text.replace(line, f'  repository: "x/y\n{line}zz: 1"\n  commit: {other}\n'),
                  text.replace(line, line + f"  commit:{other}\n"), text.replace(line, line + f"  commit: [{other}]\n"),
                  text.replace(line, line + "  commit: |\n"), text.replace(line, line + f"  commit: &x {other}\n"),
                  text.replace("pdm:\n  repository: MirumeAI/PDM\n", "pdm: x\n  repository: MirumeAI/PDM\n"))
        # YAML の読み手によって行の区切りになる字と、制御の字
        chars = tuple(text.replace(line, line + f"  # c{ch}  commit: {other}\n") for ch in ("\x85", "\u2028", "\x0c", "\x9b"))
        for says, cases in (("鍵は字のまま", keys), ("字下げは 2 つの空白", indents),
                            ("値は `:` の後に空白|見出しの行には、 値を書かない", values), ("使えない字", chars)):
            for bad in cases:
                with self.subTest(bad=bad[len(text) - 60:]):
                    (self.repo / "versions.yaml").write_text(bad, encoding="utf-8")
                    err = self.refuses("versions.yaml")
                    self.assertRegex(err, says)
        self.write_versions()
        self.assertEqual(IV.read_versions(self.repo)["pdm"]["commit"], PDM_COMMIT)

    def test_the_machine_file_is_read_as_before(self):
        """機体の設定は、これまでの読み方のまま（字のままの書き方だけを受けるのは versions.yaml）。"""
        self.machine.write_text("schema_version: 1\nmachine:\n  symbol: M01\n  kind: installed\n"
                                "profile:\n  camera:\n    camera1:\n      serial_number: \"\"\n", encoding="utf-8")
        self.assertEqual(IV.read_machine(self.machine), ("M01", "installed"))

    @unittest.skipUnless(IN_CASE, NOT_IN_CASE)
    def test_the_repository_versions_yaml_is_read_in_the_strict_form(self):
        """この Repository の versions.yaml は、字のままの書き方だけを受ける読み方で、両 Core の節がそろって読める。"""
        got = IV.sections(REPO / "versions.yaml", strict=tuple(IV.CORES))
        self.assertLessEqual(set(IV.CORES), set(got))

    def test_a_bundle_hash_that_differs_is_refused(self):
        self.write_versions(pdm_sums="0" * 64)
        self.refuses("versions.yaml の sha256sums_sha256 と違います")

    def test_a_main_wheel_that_differs_is_refused(self):
        self.write_versions(ia_wheel="0" * 64)
        self.refuses("本体の wheel の sha256")

    def test_a_changed_wheel_is_refused(self):
        (self.wh / "pdm" / "v2.0.0" / "wheelhouse" / "numpy-2.2.6-cp310-cp310-linux_x86_64.whl").write_bytes(b"x")
        self.refuses("SHA256SUMS と違います")

    def test_a_missing_wheel_is_refused(self):
        (self.wh / "pdm" / "v2.0.0" / "wheelhouse" / "numpy-2.2.6-cp310-cp310-linux_x86_64.whl").unlink()
        self.refuses("SHA256SUMS に書いたファイルが無い")

    def test_an_unlisted_wheel_is_refused(self):
        (self.wh / "ia" / "v1.0.0" / "wheelhouse" / "evil-1.0-py3-none-any.whl").write_bytes(b"e")
        self.refuses("SHA256SUMS に無いファイル")

    def test_a_link_in_the_bundle_is_refused(self):
        target = self.wh / "pdm" / "v2.0.0" / "wheelhouse" / "numpy-2.2.6-cp310-cp310-linux_x86_64.whl"
        moved = self.tmp / "np.whl"
        shutil.move(target, moved)
        target.symlink_to(moved)
        self.refuses("リンク")

    def test_a_path_outside_the_bundle_is_refused(self):
        sums = self.wh / "pdm" / "v2.0.0" / "SHA256SUMS"
        sums.write_text(sums.read_text(encoding="utf-8") + f"{'0' * 64}  ../../x\n", encoding="utf-8")
        self.write_versions(pdm_sums=sha(sums.read_bytes()))
        self.refuses("束の外")

    def test_a_release_record_of_another_commit_is_refused(self):
        rel = self.wh / "ia" / "v1.0.0" / "release.json"
        data = json.loads(rel.read_text(encoding="utf-8"))
        data["commit"] = "d" * 40
        rel.write_text(json.dumps(data), encoding="utf-8")
        self.refuses("tag・commit が versions.yaml と違います")

    def test_the_product_wheel_must_match_the_system_release(self):
        self.release.write_text(f"product_package:\n  name: fake-case\n  version: \"0.1.0\"\n  wheel_sha256: \"{'0' * 64}\"\n",
                                encoding="utf-8")
        self.refuses("System Release")

    def test_a_lock_without_hashes_is_refused(self):
        self.replace_ia_file("requirements-web.lock", "fastapi>=0.1\n")
        self.refuses("`名前==版` と `--hash=sha256:` の形ではありません")

    def test_an_ia_bundle_made_for_another_pdm_bundle_is_refused(self):
        """撮像側の束が前提にした PDM の束（release.json の pdm）が versions.yaml の pdm と違う・無いときは止める。"""
        for pdm in ({"version": "2.0.0", "tag": "v2.0.0", "sha256sums_sha256": "0" * 64},
                    {"version": "2.1.0", "tag": "v2.1.0", "sha256sums_sha256": self.pdm_sums}, None):
            with self.subTest(pdm=pdm):
                self.set_ia_release(pdm=pdm)
                self.refuses("撮像側の束が前提にした PDM の束")

    def test_a_guard_lock_that_moves_a_pdm_bundle_pin_is_refused(self):
        """門番の lock が、 PDM の束の wheel と同じ名前を違う版で固定していたら止める。"""
        self.replace_ia_file("requirements-guard.lock", "numpy==2.2.5 --hash=sha256:" + "2" * 64 + "\n")
        self.refuses(".venvs/ai に入れる numpy の版が、束の間で違います")

    def test_the_plan_pins_every_wheel_and_lock_line(self):
        """入れた後に照らす名前と版は、 束の wheel ぜんぶと lock の行ぜんぶ。"""
        self.replace_ia_file("requirements-guard.lock", "cryptography==46.0.3 --hash=sha256:" + "3" * 64 + "\n"
                             "numpy==2.2.6 --hash=sha256:" + "4" * 64 + "\n")
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        self.assertEqual({n: v for n, v, _ in p["pins"]["web"]},
                         {"fastapi": "0.128.0", "mirumeai-image-acquisition": "1.0.0", "fake-case": "0.1.0"})
        self.assertEqual({n: v for n, v, _ in p["pins"]["ai"]},
                         {"mirumeai-pdm": "2.0.0", "numpy": "2.2.6", "torch": "2.10.0+cu128", "cryptography": "46.0.3",
                          "mirumeai-image-acquisition": "1.0.0", "fake-case": "0.1.0"})
        want = IV.expectations("ai", p)
        self.assertEqual(want["pins"], p["pins"]["ai"])
        self.assertEqual(want["seed"], ["pip", "setuptools"])
        self.assertEqual(want["parts"], ["fake-case", FAKE_PARTS])       # 部品の登録は根の pyproject.toml の宣言から
        self.assertEqual(want["extras"], [["mirumeai-pdm", "web"], ["fake-case", "parts"]])
        self.assertEqual(want["package"], "fake_case")                   # doctor を呼ぶ Python の名前
        self.assertIs(want["judge"], True)                    # 判定の venv は PDM の版も照らす
        self.assertIs(IV.expectations("web", p)["judge"], False)

    # ---- 人が置いたカメラの部品

    GX = "fakecam-1.2.3-py3-none-any.whl"

    @staticmethod
    def wheel(files=None, dist="fakecam-1.2.3"):
        """本物の zip の偽の wheel（中身の道を照らすため）。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, data in (files or {"fakecam/__init__.py": b"x"}).items():
                z.writestr(name, data)
            z.writestr(f"{dist}.dist-info/METADATA", b"Name: fakecam\n")
        return buf.getvalue()

    def put_camera(self, files):
        d = self.wh / "camera"
        d.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (d / name).write_bytes(data)
        return d

    def marks(self):
        return {n: json.loads((self.root / ".venvs" / n / IV.MARK).read_text(encoding="utf-8")) for n in ("web", "ai")}

    def test_without_camera_wheels_the_plan_and_the_marks_are_unchanged(self):
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        self.assertEqual(p["camera"], [])
        for name in ("web", "ai"):
            self.assertEqual(IV.venv_want(p, name), dict(p["want"], venv=name))
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len([c for c in made[-1].calls if "install" in c]), 7)
        self.assertFalse([c for c in made[-1].calls if IV._FILES in c])
        self.assertNotIn("カメラの部品", out)
        self.assertFalse(any("camera" in m for m in self.marks().values()))

    def test_camera_wheels_go_into_the_web_venv_last_with_hashes(self):
        data = self.wheel()
        cam = self.put_camera({self.GX: data})
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"fakecam 1.2.3・sha256 {sha(data)}", out)
        installs = [c for c in made[-1].calls if "install" in c]
        self.assertEqual(len(installs), 8)                       # Web 4（最後がカメラの部品）・判定 4
        camera = installs[3]
        self.assertTrue(camera[-1].endswith("camera.txt"))
        self.assertEqual(camera[camera.index("--find-links") + 1], str(cam))
        self.assertEqual(camera.count("--find-links"), 1)
        for flag in ("--no-index", "--require-hashes"):
            self.assertIn(flag, camera)
        self.assertNotIn("--no-deps", camera)                   # 依存は、もう入った物と camera/ の wheel で満たす
        calls = made[-1].calls
        files_at = [i for i, c in enumerate(calls) if IV._FILES in c]
        self.assertEqual(len(files_at), 1)                       # 上書きの照らしは、カメラの部品を入れる前に 1 回
        self.assertLess(files_at[0], calls.index(camera))
        req = (self.root / ".cache" / "install_venvs" / "req-web" / "camera.txt").read_text(encoding="utf-8")
        self.assertEqual(req, f"fakecam==1.2.3 --hash=sha256:{sha(data)}\n")
        marks = self.marks()
        self.assertEqual(marks["web"]["camera"], [{"name": "fakecam", "version": "1.2.3", "sha256": sha(data),
                                                   "file": self.GX}])
        # serve は 2 つの印を venv の鍵のほかは同じかで比べるので、判定の印にも同じ物を書く（入れるのは Web だけ）
        self.assertEqual({k: v for k, v in marks["web"].items() if k != "venv"},
                         {k: v for k, v in marks["ai"].items() if k != "venv"})
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        self.assertIn(["fakecam", "1.2.3", None], IV.expectations("web", p)["pins"])   # 照らしで束の外の物と言わない
        self.assertNotIn("fakecam", [n for n, _, _ in IV.expectations("ai", p)["pins"]])

    def test_a_changed_or_added_camera_wheel_needs_a_rebuild_of_both_venvs(self):
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        self.put_camera({self.GX: self.wheel()})
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1)
        self.assertIn(".venvs/web は作り直しが要ります", err)
        rc, out, err, made = self.main("--rebuild")
        self.assertEqual(rc, 0, out + err)
        other = self.wheel({"fakecam/__init__.py": b"other"})
        self.put_camera({self.GX: other})
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1)
        self.assertIn(".venvs/web は作り直しが要ります", err)
        rc, out, err, made = self.main("--rebuild")
        self.assertEqual(rc, 0, out + err)
        self.assertEqual({m["camera"][0]["sha256"] for m in self.marks().values()}, {sha(other)})

    def test_a_camera_wheel_that_cannot_be_combined_stops_and_leaves_the_mark_partial(self):
        self.put_camera({self.GX: self.wheel()})
        rc, out, err, made = self.main(run=lambda log: FakeRun(log, fail_install=4))
        self.assertEqual(rc, 1, out + err)
        self.assertIn("Web の venv に入った物と組めません", err)
        self.assertIn("カメラの部品は入れていません", err)
        web = json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))
        self.assertFalse(web["complete"])
        self.assertFalse((self.root / ".venvs" / "ai").exists())

    def test_a_camera_wheel_that_overwrites_an_installed_file_is_refused_before_installing(self):
        self.put_camera({self.GX: self.wheel({"fakecam/__init__.py": b"x", "mirumeai_ia/__init__.py": b"y"})})
        run = lambda log: FakeRun(log, files_out=json.dumps(["fastapi/__init__.py", "mirumeai_ia/__init__.py"]))
        rc, out, err, made = self.main(run=run)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("もう入った配布物のファイルを上書きします（mirumeai_ia/__init__.py", err)
        self.assertEqual(len([c for c in made[-1].calls if "install" in c]), 3)      # カメラの部品は入れていない
        web = json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))
        self.assertFalse(web["complete"])

    def test_a_camera_wheel_that_adds_files_to_another_distribution_folder_is_refused(self):
        self.put_camera({self.GX: self.wheel({"fakecam/__init__.py": b"x", "mirumeai_ia/extra.py": b"y"})})
        run = lambda log: FakeRun(log, files_out=json.dumps(["mirumeai_ia/__init__.py", "../../../bin/uvicorn"]))
        rc, out, err, made = self.main(run=run)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("ほかの配布物の頭のフォルダ mirumeai_ia/ に置きます", err)
        self.assertEqual(len([c for c in made[-1].calls if "install" in c]), 3)      # カメラの部品は入れていない

    def test_entry_points_without_commands_are_accepted(self):
        data = self.wheel({"fakecam/__init__.py": b"x",
                           "fakecam-1.2.3.dist-info/entry_points.txt": b"[some.group]\nx = fakecam:x\n[console_scripts]\n# none\n"})
        self.put_camera({self.GX: data})
        rc, out, err, made = self.main("--check")
        self.assertEqual(rc, 0, out + err)

    def test_a_camera_wheel_named_like_a_bundle_or_seed_distribution_is_refused(self):
        for label, name in (("違う版", "fastapi-0.129.0-py3-none-any.whl"), ("同じ版", "fastapi-0.128.0-py3-none-any.whl"),
                            ("撮像側", "mirumeai_image_acquisition-1.0.0-py3-none-any.whl"),
                            ("種", "pip-25.0-py3-none-any.whl"), ("種", "setuptools-80.0-py3-none-any.whl")):
            with self.subTest(label=label, name=name):
                d = self.wh / "camera"
                if d.exists():
                    shutil.rmtree(d)
                self.put_camera({name: self.wheel(dist=name.split("-py3")[0])})
                self.refuses("束か venv の種（pip・setuptools）で入れる配布物と同じ名前です")

    def test_bad_things_in_the_camera_folder_are_refused(self):
        cases = {
            "wheel でない": (lambda d: (d / "README.txt").write_text("x", encoding="utf-8"), "wheel でない物かリンク"),
            "フォルダ": (lambda d: (d / "sub").mkdir(), "wheel でない物かリンク"),
            "リンク": (lambda d: (d / "fakecam-2.0-py3-none-any.whl").symlink_to(self.tmp / "elsewhere.whl"),
                       "wheel でない物かリンク"),
            "名前の形": (lambda d: (d / "fakecam.whl").write_bytes(self.wheel()), "wheel の名前の形が違います"),
            "同じ名前": (lambda d: [(d / "fakecam-1.0-py3-none-any.whl").write_bytes(self.wheel()),
                                   (d / "fakecam-2.0-py3-none-any.whl").write_bytes(self.wheel())],
                         "fakecam の wheel が 2 つあります"),
            "zip でない": (lambda d: (d / self.GX).write_bytes(b"not a zip"), "wheel として読めません"),
            ".pth": (lambda d: (d / self.GX).write_bytes(self.wheel({"fakecam/__init__.py": b"x", "evil.pth": b"import os"})),
                     "sitecustomize・usercustomize か .data/ を持ちます"),
            ".data": (lambda d: (d / self.GX).write_bytes(
                self.wheel({"fakecam/__init__.py": b"x", "fakecam-1.2.3.data/scripts/run": b"x"})), "sitecustomize・usercustomize か .data/ を持ちます"),
            "外を指す道": (lambda d: (d / self.GX).write_bytes(self.wheel({"../escape.py": b"x"})), "外を指す道があります"),
            # pip は道を直して置くので、書かれた文字で照らす照らしを外せる形
            "崩れた道 ./": (lambda d: (d / self.GX).write_bytes(self.wheel({"./fakecam/__init__.py": b"x"})), "形の崩れた道"),
            "崩れた道 .pth/.": (lambda d: (d / self.GX).write_bytes(self.wheel({"fakecam/__init__.py": b"x", "zz.pth/.": b"x"})),
                                "形の崩れた道"),
            "崩れた道 //": (lambda d: (d / self.GX).write_bytes(self.wheel({"fakecam//__init__.py": b"x"})), "形の崩れた道"),
            "sitecustomize": (lambda d: (d / self.GX).write_bytes(self.wheel({"fakecam/__init__.py": b"x",
                                                                                "sitecustomize.py": b"x"})), "sitecustomize"),
            "sitecustomize のパッケージ": (lambda d: (d / self.GX).write_bytes(
                self.wheel({"usercustomize/__init__.py": b"x"})), "sitecustomize"),
            "命令": (lambda d: (d / self.GX).write_bytes(self.wheel({
                "fakecam/__init__.py": b"x",
                "fakecam-1.2.3.dist-info/entry_points.txt": b"[console_scripts]\npip = fakecam:main\n"})), "命令（console_scripts"),
        }
        (self.tmp / "elsewhere.whl").write_bytes(self.wheel())
        for label, (make, says) in cases.items():
            with self.subTest(label):
                d = self.wh / "camera"
                if d.exists():
                    shutil.rmtree(d)
                d.mkdir()
                make(d)
                self.refuses(says)
        shutil.rmtree(self.wh / "camera")
        (self.wh / "camera").symlink_to(self.tmp)
        self.refuses("フォルダにします（リンクは断る）")

    def test_an_empty_camera_folder_installs_nothing(self):
        (self.wh / "camera").mkdir()
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("camera/ に wheel がありません", out)
        self.assertEqual(len([c for c in made[-1].calls if "install" in c]), 7)

    def test_check_shows_the_camera_wheels_and_makes_nothing(self):
        data = self.wheel()
        self.put_camera({self.GX: data})
        rc, out, err, made = self.main("--check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn(f"fakecam 1.2.3・sha256 {sha(data)}", out)
        self.assertFalse((self.root / ".venvs").exists())

    def test_check_makes_nothing(self):
        rc, out, err, made = self.main("--check")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("何も作っていません", out)
        self.assertFalse((self.root / ".venvs").exists())
        self.assertFalse((self.root / ".cache").exists())

    def test_a_venv_it_did_not_make_is_not_removed(self):
        (self.root / ".venvs" / "web").mkdir(parents=True)
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1)
        self.assertIn("この道具が作った venv ではありません", err)
        self.assertTrue((self.root / ".venvs" / "web").is_dir())

    def test_a_pip_failure_leaves_a_partial_mark_that_rebuild_replaces(self):
        """pip が途中で落ちた venv には「途中」の印が残り、 --rebuild で作り直せる。"""
        rc, out, err, made = self.main(run=lambda log: FakeRun(log, fail_install=2))
        self.assertEqual(rc, 1, out + err)
        mark = json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))
        self.assertFalse(mark["complete"])
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1)
        self.assertIn("--rebuild", err)
        rc, out, err, made = self.main("--rebuild")
        self.assertEqual(rc, 0, out + err)
        self.assertTrue(json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))["complete"])

    def test_a_failed_check_after_install_leaves_the_mark_partial(self):
        rc, out, err, made = self.main(run=lambda log: FakeRun(log, verify_out='["mirumeai-pdm の版が違います"]'))
        self.assertEqual(rc, 1)
        mark = json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))
        self.assertFalse(mark["complete"])
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1)
        self.assertIn("--rebuild", err)
        rc, out, err, made = self.main("--rebuild")
        self.assertEqual(rc, 0, out + err)

    # ---- 権限

    def test_the_tool_narrows_the_umask_while_it_runs_and_gives_it_back(self):
        """流す間は、umask にグループとほかの人の書き込みを外す 022 を足し（venv と pip の子にも効く。もっと絞った物は
        そのまま）、作った物にグループとほかの人の書き込みが無い。終わりに流す人の umask へ戻す。"""
        seen = []

        class Seeing(FakeRun):
            def __call__(inner, cmd, capture=False, env=None):
                seen.append(umask_now())
                return FakeRun.__call__(inner, cmd, capture, env)

        for given, want in ((0o002, 0o022), (0o000, 0o022), (0o077, 0o077), (0o007, 0o027)):
            with self.subTest(umask=oct(given)):
                for d in (".venvs", ".cache"):
                    shutil.rmtree(self.root / d, ignore_errors=True)
                seen.clear()
                os.umask(given)
                try:
                    rc, out, err, made = self.main(run=Seeing)
                    after = umask_now()
                finally:
                    os.umask(0o022)
                self.assertEqual(rc, 0, out + err)
                self.assertTrue(seen)
                self.assertEqual(set(seen), {want})
                self.assertEqual(after, given, "流す人の umask へ戻していない")
                for p in (self.root / ".venvs", self.root / ".venvs" / "web", self.root / ".venvs" / "ai" / "bin",
                          self.root / ".cache", self.root / ".cache" / "install_venvs" / "req-ai",
                          self.root / ".cache" / "install_venvs" / "req-ai" / "pdm.txt",
                          self.root / ".venvs" / "web" / IV.MARK):
                    self.assertEqual(p.lstat().st_mode & 0o022, 0, p)

    def test_places_others_can_write_stop_before_anything_is_made(self):
        """根・.venvs・.cache・.cache/install_venvs とその中に、グループかほかの人が書ける物があれば、--check でも、
        何も作らずに止める。今ある物の権限は直さない。"""
        logdir = self.root / ".cache" / "install_venvs"
        (logdir / "req-web").mkdir(parents=True)
        (logdir / "req-web" / "ia.txt").write_text("前の回\n", encoding="utf-8")
        (self.root / ".venvs").mkdir()
        for target, bits in ((self.root, 0o020), (self.root / ".venvs", 0o020), (self.root / ".venvs", 0o002),
                             (self.root / ".cache", 0o020), (logdir, 0o020), (logdir / "req-web", 0o002),
                             (logdir / "req-web" / "ia.txt", 0o020)):
            with self.subTest(target=str(target.relative_to(self.tmp)), bits=oct(bits)):
                mode = target.stat().st_mode & 0o777
                os.chmod(target, mode | bits)
                try:
                    for args in ((), ("--check",), ("--rebuild",)):
                        rc, out, err, made = self.main(*args)
                        self.assertEqual(rc, 1, out + err)
                        self.assertIn("グループを含む", err)
                        self.assertIn(f"（1 個: {target.relative_to(self.root) if target != self.root else '.'}）", err)
                        self.assertFalse((self.root / ".venvs" / "web").exists(), "止めたのに venv を作った")
                        self.assertEqual(target.stat().st_mode & 0o777, mode | bits, "今ある物の権限を直した")
                finally:
                    os.chmod(target, mode)
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)

    def test_a_venv_folder_others_can_write_is_not_rebuilt(self):
        """入れた venv の dir にグループが書ければ、--rebuild でも消さずに止める（人が確かめる）。"""
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        venv = self.root / ".venvs" / "ai"
        os.chmod(venv, 0o775)
        for args in ((), ("--check",), ("--rebuild",)):
            with self.subTest(args=args):
                rc, out, err, made = self.main(*args)
                self.assertEqual(rc, 1, out + err)
                self.assertIn(".venvs/ai", err)
                self.assertTrue((venv / IV.MARK).is_file(), "消した")

    def test_a_development_version_of_the_product_is_refused(self):
        """開発の版・前出しの版の製品のパッケージは、据え付けた機体と顧客に見せる機体に入れない。"""
        for version in ("0.1.0.dev0", "0.1.0a1", "0.1.0b2", "0.1.0rc1", "1.0.0.dev3", "0.1.0.post1.dev0"):
            with self.subTest(version=version):
                self.release.write_text("product_package:\n  name: fake-case\n"
                                        f"  version: \"{version}\"\n  wheel_sha256: \"{self.product_wheel}\"\n",
                                        encoding="utf-8")
                self.refuses("出した版（X.Y.Z）ではありません")
        for version in ("0.1.0", "1.2", "0.1.0.post1"):
            self.assertTrue(IV.RELEASED_VERSION.fullmatch(version), version)

    def test_a_link_inside_the_install_cache_stops(self):
        """pip に読ませる要求のファイルの置き場（.cache/install_venvs）の中のリンクは、入れる前も入れた後も断る。"""
        elsewhere = self.tmp / "elsewhere.txt"
        elsewhere.write_text("x", encoding="utf-8")
        logdir = self.root / ".cache" / "install_venvs" / "req-web"
        logdir.mkdir(parents=True)
        (logdir / "ia.txt").symlink_to(elsewhere)
        for args in ((), ("--check",)):
            rc, out, err, made = self.main(*args)
            self.assertEqual(rc, 1, out + err)
            self.assertIn("req-web/ia.txt（リンク）", err)
        (logdir / "ia.txt").unlink()

        class Linking(FakeRun):
            def __call__(inner, cmd, capture=False, env=None):
                out = FakeRun.__call__(inner, cmd, capture, env)
                if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv" and cmd[-1].endswith("web"):
                    (logdir / "late.txt").symlink_to(elsewhere)
                return out

        rc, out, err, made = self.main(run=Linking)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("late.txt（リンク）", out)
        self.assertFalse(json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))["complete"])

    def test_things_of_another_user_stop(self):
        """流した利用者の持ち物でない物があれば止める（持ち主を変えられないので、流した利用者の番号を偽る）。"""
        with mock.patch.object(IV.os, "geteuid", return_value=os.geteuid() + 1):
            rc, out, err, made = self.main()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("ほかの人の持ち物", err)
        self.assertFalse((self.root / ".venvs").exists())

    def test_linked_places_stop(self):
        """.venvs・.cache・.cache/install_venvs がリンクなら、先に作らずに止める。"""
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        for rel in (".venvs", ".cache", ".cache/install_venvs"):
            with self.subTest(rel=rel):
                link = self.root / rel
                link.parent.mkdir(parents=True, exist_ok=True)
                link.symlink_to(elsewhere)
                try:
                    for args in ((), ("--check",)):
                        rc, out, err, made = self.main(*args)
                        self.assertEqual(rc, 1, out + err)
                        self.assertIn(f"{rel} がリンクです", err)
                        self.assertEqual(list(elsewhere.iterdir()), [], "リンクの先に作った")
                finally:
                    link.unlink()

    def test_a_venv_left_open_after_install_stops_and_stays_partial(self):
        """入れた後に venv の中にグループかほかの人が書ける物があれば、印を途中のままにして止める。"""
        class Opening(FakeRun):
            def __call__(inner, cmd, capture=False, env=None):
                out = FakeRun.__call__(inner, cmd, capture, env)
                if "-m" in cmd and cmd[cmd.index("-m") + 1] == "venv" and cmd[-1].endswith("web"):
                    f = Path(cmd[-1], "bin", "activate")
                    f.write_text("x", encoding="utf-8")
                    os.chmod(f, 0o664)
                return out

        rc, out, err, made = self.main(run=Opening)
        self.assertEqual(rc, 1, out + err)
        self.assertIn(".venvs/web/bin/activate", out)
        self.assertIn(".venvs/web の照らしが通りません", err)
        self.assertFalse(json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))["complete"])
        self.assertFalse((self.root / ".venvs" / "ai").exists(), "止めた後に判定の venv を作った")

    def test_check_finds_a_file_others_can_write_in_a_venv(self):
        """--check は、入れた venv の中を全部歩いて照らす。リンクは持ち主だけを見る（先は見ない）。"""
        rc, out, err, made = self.main()
        self.assertEqual(rc, 0, out + err)
        site = self.root / ".venvs" / "ai" / "lib"
        site.mkdir()
        (site / "link").symlink_to("/")                     # リンクの権限と先は見ない
        rc, out, err, made = self.main("--check")
        self.assertEqual(rc, 0, out + err)
        (site / "x.py").write_text("", encoding="utf-8")
        os.chmod(site / "x.py", 0o646)
        rc, out, err, made = self.main("--check")
        self.assertEqual(rc, 1, out + err)
        self.assertIn(".venvs/ai の照らし: 合わない", out)
        self.assertIn(".venvs/ai/lib/x.py", out)
        self.assertNotIn("lib/link", out)
        ai_py = str(self.root / ".venvs" / "ai" / "bin" / "python")
        self.assertFalse([c for r in made for c in r.calls if c[0] == ai_py], "開いた venv の python を流した")
        self.assertTrue(json.loads((self.root / ".venvs" / "ai" / IV.MARK).read_text(encoding="utf-8"))["complete"],
                        "--check が印を書いた")
        # ふつうに流し直すと、印を途中に戻して止まる（開いた venv の python は流さない）
        rc, out, err, made = self.main()
        self.assertEqual(rc, 1, out + err)
        self.assertIn(".venvs/ai/lib/x.py", out)
        self.assertFalse([c for r in made for c in r.calls if c[0] == ai_py], "開いた venv の python を流した")
        self.assertFalse(json.loads((self.root / ".venvs" / "ai" / IV.MARK).read_text(encoding="utf-8"))["complete"])

    def test_a_file_others_can_write_in_the_install_cache_after_install_stops(self):
        """入れている間に .cache/install_venvs の中に開いた物ができたら、印を途中のままにして止める。"""
        class Opening(FakeRun):
            def __call__(inner, cmd, capture=False, env=None):
                out = FakeRun.__call__(inner, cmd, capture, env)
                if "install" in cmd:
                    f = Path(cmd[-1])                        # pip に読ませた要求のファイル
                    os.chmod(f, 0o664)
                return out

        rc, out, err, made = self.main(run=Opening)
        self.assertEqual(rc, 1, out + err)
        self.assertIn(".cache/install_venvs/req-web/", out)
        self.assertFalse(json.loads((self.root / ".venvs" / "web" / IV.MARK).read_text(encoding="utf-8"))["complete"])

    def test_the_verify_script_reads_records(self):
        """入れた後の照らし（_VERIFY）を、偽の dist-info に本物の Python で流す（版・extra の依存・部品の登録）。"""
        site = self.tmp / "site"

        def dist(name, version, requires=(), eps=""):
            d = site / f"{name.replace('-', '_')}-{version}.dist-info"
            d.mkdir(parents=True)
            meta = [f"Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}"] + [f"Requires-Dist: {r}" for r in requires]
            (d / "METADATA").write_text("\n".join(meta) + "\n", encoding="utf-8")
            if eps:
                (d / "entry_points.txt").write_text(eps, encoding="utf-8")

        checks = "[mirumeai_pdm.extra_checks]\ncheck_a = x.y\n"
        methods = "[mirumeai_pdm.extract_methods]\nmethod_b = x:B\nmethod_a = x:A\n"
        dist("mirumeai-test-product", "0.1.0", ['mirumeai-test-np==2.2.6; extra == "parts"'], checks + "\n" + methods)
        dist("mirumeai-test-np", "2.2.6")
        # 試験を流す Python にもとからある配布物は、 venv を作るときに入る物と同じ扱いにする（偽の物だけを照らす）
        base = subprocess.run([sys_executable(), "-B", "-c", "import importlib.metadata as m, json, re; print(json.dumps("
                               "sorted({re.sub(r'[-_.]+', '-', d.metadata['Name'] or '').lower() for d in m.distributions()})))"],
                              capture_output=True, text=True, check=True, env=dict(os.environ, PYTHONNOUSERSITE="1"))
        seed = json.loads(base.stdout)
        want = {"pins": [["mirumeai-test-np", "2.2.6", None], ["mirumeai-test-product", "0.1.0", None],
                         ["mirumeai-test-never", "1.0", "python_version < '3'"]],
                "seed": seed, "extras": [["mirumeai-test-product", "parts"]],
                "parts": ["mirumeai-test-product", FAKE_PARTS], "package": "mirumeai_test_product"}

        def run(cmd, capture=False, env=None):
            if "check" in cmd or "doctor" in cmd:           # pip check と製品の doctor（別の試験で照らす）は通す
                return ""
            e = dict(os.environ, PYTHONPATH=str(site), PYTHONNOUSERSITE="1")
            return subprocess.run([sys_executable(), "-B"] + [str(c) for c in cmd[1:] if c != "-I"], env=e,
                                  capture_output=True, text=True, check=True).stdout

        def ours(bad):
            return [b for b in bad if "mirumeai-test" in b]

        self.assertEqual(ours(IV.verify(run, Path("py"), want)), [])     # 環境の印が合わない行は数えない
        dist("mirumeai-test-extra", "1.0")
        self.assertIn("mirumeai-test-extra は束に無い配布物です", IV.verify(run, Path("py"), want))
        shutil.rmtree(site / "mirumeai_test_extra-1.0.dist-info")
        shutil.rmtree(site / "mirumeai_test_np-2.2.6.dist-info")
        dist("mirumeai-test-np", "2.2.5")
        bad = IV.verify(run, Path("py"), want)
        self.assertTrue(any("[parts]" in b for b in bad), bad)
        self.assertIn("mirumeai-test-np の版が 2.2.6 ではありません（2.2.5）", bad)
        shutil.rmtree(site / "mirumeai_test_np-2.2.5.dist-info")
        bad = IV.verify(run, Path("py"), want)
        self.assertIn("mirumeai-test-np が入っていません", bad)
        dist("mirumeai-test-np", "2.2.6")
        self.assertEqual(ours(IV.verify(run, Path("py"), want)), [])
        for group, names in (("mirumeai_pdm.extra_checks", ["check_b"]), ("mirumeai_pdm.extract_methods", ["method_a"]),
                             ("mirumeai_pdm.extract_methods", ["method_a", "method_b", "method_c"])):
            with self.subTest(group=group, names=names):
                bad = IV.verify(run, Path("py"), dict(want, parts=["mirumeai-test-product", dict(FAKE_PARTS, **{group: names})]))
                self.assertEqual([b for b in bad if "登録していません" in b],
                                 [f"mirumeai-test-product が {group} に {'・'.join(names)} だけを 1 つずつ登録していません"])

        def product_eps(eps):
            shutil.rmtree(site / "mirumeai_test_product-0.1.0.dist-info")
            dist("mirumeai-test-product", "0.1.0", ['mirumeai-test-np==2.2.6; extra == "parts"'], eps)
            return [b for b in IV.verify(run, Path("py"), want) if "登録していません" in b]

        # 製品の wheel の登録が古い・余る・重なる（群ごとに照らす）
        self.assertEqual(product_eps(checks), ["mirumeai-test-product が mirumeai_pdm.extract_methods に method_a・method_b "
                                               "だけを 1 つずつ登録していません"])
        self.assertEqual(len(product_eps(methods)), 1)
        self.assertEqual(len(product_eps(checks + "\n" + methods + "method_c = x:C\n")), 1)
        self.assertEqual(len(product_eps(checks + "check_b = x.z\n\n" + methods)), 1)
        self.assertEqual(len(product_eps(checks + "\n" + methods + "method_a = x:Q\n")), 1)        # 同じ名前を 2 度
        self.assertEqual(len(product_eps(checks + "check_a = x.z\n\n" + methods)), 1)
        self.assertEqual(product_eps(checks + "\n" + methods), [])

        def undeclared(eps, groups):
            shutil.rmtree(site / "mirumeai_test_product-0.1.0.dist-info")
            dist("mirumeai-test-product", "0.1.0", ['mirumeai-test-np==2.2.6; extra == "parts"'], eps)
            return [b for b in IV.verify(run, Path("py"), dict(want, parts=["mirumeai-test-product", groups]))
                    if "宣言に無い群" in b or "登録していません" in b]

        # 宣言に無い mirumeai_pdm. の群への登録も止める（宣言が無いときも。setup.cfg などで足した登録を見逃さない）
        self.assertEqual(undeclared(checks + "\n" + methods + "\n[mirumeai_pdm.other]\nx = y:Z\n", FAKE_PARTS),
                         ["mirumeai-test-product が、 宣言に無い群 mirumeai_pdm.other に登録しています"])
        self.assertEqual(undeclared(checks, {}),
                         ["mirumeai-test-product が、 宣言に無い群 mirumeai_pdm.extra_checks に登録しています"])
        self.assertEqual(undeclared("[other.group]\nx = y:Z\n", {}), [])         # ほかの群は読まない
        self.assertEqual(undeclared("", {}), [])

    # ---- 根の pyproject.toml の宣言（案件に依る所。MirumeAI/.github#237）

    def test_the_declaration_is_read_from_the_root_pyproject(self):
        """製品の名前・Python の名前・extra・部品の登録を、根の pyproject.toml から読む（群は mirumeai_pdm. の物だけ）。"""
        d = IV.read_declaration(self.repo)
        self.assertEqual(d, {"name": "fake-case", "package": "fake_case", "extras": ["parts"], "parts": FAKE_PARTS,
                             "install_set_extra": [], "build_requires": ["setuptools==84.2.1", "wheel==0.48.0"],
                             "build_wheels": {}})

    def test_without_part_registrations_or_extras_nothing_more_is_checked(self):
        """部品の登録の宣言が無ければ、mirumeai_pdm. の群に登録が無いことを照らし、extra が無ければ PDM の [web] だけを照らす。
        ほかの群の登録は読まない。"""
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "fake-case"\n\n[project.entry-points."other.group"]\nx = "fake_case:x"\n', encoding="utf-8")
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        want = IV.expectations("ai", p)
        self.assertEqual(want["parts"], ["fake-case", {}])
        self.assertEqual(want["extras"], [["mirumeai-pdm", "web"]])
        self.assertNotIn("parts", IV.expectations("web", p))
        self.assertEqual(IV.expectations("web", p)["extras"], [])

    def test_every_extra_is_checked_in_the_judge_venv(self):
        (self.repo / "pyproject.toml").write_text(
            '[project]\nname = "fake-case"\n[project.optional-dependencies]\ngpu = ["torch==2.10.0"]\nparts = []\n',
            encoding="utf-8")
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        self.assertEqual(IV.expectations("ai", p)["extras"],
                         [["mirumeai-pdm", "web"], ["fake-case", "gpu"], ["fake-case", "parts"]])

    def test_a_product_of_another_name_than_the_declaration_is_refused(self):
        """System Release の製品の名前が、木の pyproject.toml の [project].name と違えば止める（正規の形で比べる）。"""
        (self.repo / "pyproject.toml").write_text('[project]\nname = "other-case"\n', encoding="utf-8")
        self.refuses("[project].name（other-case）と違います")
        (self.repo / "pyproject.toml").write_text('[project]\nname = "Fake_Case"\n', encoding="utf-8")
        p = IV.plan(self.repo, self.wh, self.machine, self.release)
        self.assertEqual(IV.expectations("ai", p)["package"], "Fake_Case")

    def test_declarations_of_a_wrong_form_are_refused(self):
        cases = {
            "pyproject.toml が無い": (None, "根に pyproject.toml がありません"),
            "TOML でない": ("[project\nname = 1\n", "pyproject.toml を読めません"),
            "複数行の文字列": ('[project]\nname = "fake-case"\ndescription = """x\ny"""\n', "複数行の文字列は読めません"),
            "[project] が無い": ('[tool.x]\na = 1\n', "[project] がありません"),
            "名前が無い": ("[project]\nversion = '1'\n", "[project].name に、使えない字があります"),
            "名前が道": ('[project]\nname = "../x"\n', "[project].name に、使えない字があります"),
            "名前が数字で始まる": ('[project]\nname = "3d-part"\n', "[project].name に、使えない字があります"),
            "名前に点": ('[project]\nname = "a.b"\n', "[project].name に、使えない字があります"),
            "extra が並びでない": ('[project]\nname = "x"\n[project.optional-dependencies]\nparts = "numpy"\n',
                                   "[project.optional-dependencies] の形が違います"),
            "登録が表でない": ('[project]\nname = "x"\n[project.entry-points]\n"mirumeai_pdm.extra_checks" = "x"\n',
                               "[project.entry-points.\"mirumeai_pdm.extra_checks\"] の形が違います"),
            "登録が空": ('[project]\nname = "x"\n[project.entry-points."mirumeai_pdm.extra_checks"]\n',
                         "[project.entry-points.\"mirumeai_pdm.extra_checks\"] の形が違います"),
            "登録の入口が文字列でない": ('[project]\nname = "x"\n[project.entry-points."mirumeai_pdm.extra_checks"]\na = 1\n',
                                         "[project.entry-points.\"mirumeai_pdm.extra_checks\"] の形が違います"),
            "読まない鍵": ('[project]\nname = "x"\n[tool.mirumeai]\ninstall-set-extras = ["docs"]\n',
                           "[tool.mirumeai] に、読まない鍵があります: install-set-extras"),
            "足す物が並びでない": ('[project]\nname = "x"\n[tool.mirumeai]\ninstall-set-extra = "docs"\n',
                                   "install-set-extra は、根の物の名前（文字列）の並びにします"),
            "同じ鍵を 2 度": ('[project]\nname = "x"\nname = "y"\n', "を 2 度書いています"),
            "組み立ての道具の wheel が表でない": ('[project]\nname = "x"\n[tool.mirumeai]\nbuild-wheels = ["a.whl"]\n',
                                                  "[tool.mirumeai.build-wheels] は、組み立ての道具の wheel のファイルの名前ごとに"),
            "組み立ての道具の名前が wheel でない": ('[project]\nname = "x"\n[tool.mirumeai.build-wheels]\n'
                                                    '"setuptools-84.2.1.tar.gz" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
                                                    "[tool.mirumeai.build-wheels] は、組み立ての道具の wheel のファイルの名前ごとに"),
            "組み立ての道具の sha256 が 64 桁でない": ('[project]\nname = "x"\n[tool.mirumeai.build-wheels]\n'
                                                       '"setuptools-84.2.1-py3-none-any.whl" = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"\n',
                                                       "[tool.mirumeai.build-wheels] は、組み立ての道具の wheel のファイルの名前ごとに"),
            "組み立ての道具の要求が並びでない": ('[build-system]\nrequires = "setuptools"\n[project]\nname = "x"\n',
                                                 "[build-system].requires の形が違います"),
        }
        for label, (text, says) in cases.items():
            with self.subTest(label):
                path = self.repo / "pyproject.toml"
                if text is None:
                    path.unlink()
                else:
                    path.write_text(text, encoding="utf-8")
                with self.assertRaises(IV.Stop) as cm:
                    IV.read_declaration(self.repo)
                self.assertIn(says, str(cm.exception))
        (self.repo / "pyproject.toml").write_text(FAKE_PYPROJECT, encoding="utf-8")
        target = self.tmp / "elsewhere.toml"
        target.write_text(FAKE_PYPROJECT, encoding="utf-8")
        (self.repo / "pyproject.toml").unlink()
        (self.repo / "pyproject.toml").symlink_to(target)
        with self.assertRaisesRegex(IV.Stop, "根に pyproject.toml がありません"):
            IV.read_declaration(self.repo)

    @unittest.skipUnless(IN_CASE, NOT_IN_CASE)
    def test_the_repository_declaration_is_read(self):
        """この Repository の根の pyproject.toml を、宣言の読み方で読める（Python の名前のパッケージがある）。"""
        d = IV.read_declaration(REPO)
        self.assertTrue((REPO / "src" / d["package"] / "__init__.py").is_file(), d["package"])


try:
    import tomllib                                                  # Python 3.11 から。3.10 では読み手の照らしだけ流す
except ImportError:
    tomllib = None

#: どちらの読み手も読める TOML（tomllib のある Python では、同じに読むことを照らす）
TOML_GOOD = {
    "偽の案件の宣言": FAKE_PYPROJECT,
    "空": "",
    "いろいろな書き方": (
        "# 注釈\n"
        "title = \"x\"   # 行末の注釈\n"
        "\"quoted key\" = 'literal \\ value'\n"
        "dotted.key.a = 1\n"
        "dotted . key . b = 2.5e3\n"
        "site.\"example.com\" = true\n"
        "\n"
        "[a]\n"
        "b = [1, 2, [3, \"4\"], {x = 1}, ]\n"
        "c = {d = \"e\", f.g = \"h\"}\n"
        "e = {}\n"
        "date = 1979-05-27T07:32:00Z\n"
        "date2 = 1979-05-27 07:32:00.5+09:00\n"
        "day = 1979-05-27\n"
        "local = 07:32:00.123\n"
        "nums = [0xDEAD_BEEF, 0o755, 0b1010, -0, +1_000, 6.02e+23, -inf, nan, 1e10]\n"
        "esc = \"a\\tb\\u3042\\U0001F600\\\\\\\"\"\n"
        "jp = \"日本語\"\n"
        "[a.sub]\n"
        "x = 1\n"
        "[[arr]]\n"
        "n = 1\n"
        "v = [1]\n"
        "[arr.inner]\n"
        "y = 2\n"
        "[[arr]]\n"
        "n = 2\n"
        "[arr.inner]\n"
        "y = 3\n"
        "[arr.v]\n"
        "z = 1\n"
        "[project]\n"
        "name = \"x\"\n"
        "[project.entry-points.\"mirumeai_pdm.x\"]\n"
        "a = \"b\"\n"
        "[tool.mirumeai]\n"
        "install-set-extra = [\n"
        "  \"docs\",  # 注釈\n"
        "  'plugins',\n"
        "\n"
        "]\n"),
    "改行が CRLF": "[project]\r\nname = \"x\"\r\n# 注釈\r\n",
    "表を後から見出しで書く": "[x.y]\na = 1\n[x]\nb = 2\n",
    "点でつないだ鍵の表に同じ節で足す": "[x]\na.b = 1\na.c = 2\n",
}

#: どちらの読み手も読まない TOML（tomllib のある Python では、tomllib も断ることを照らす）
TOML_BAD = {
    "同じ鍵": "a = 1\na = 2\n",
    "同じ表": "[a]\n[a]\n",
    "行の中の表に見出しで足す": "a = {b = 1}\n[a]\n",
    "行の中の表に点の鍵で足す": "a = {b = 1}\na.c = 2\n",
    "点の鍵の表を見出しで書く": "[a]\nb.c = 1\n[a.b]\n",
    "見出しの表を点の鍵で書き直す": "[a.b]\nc = 1\n[a]\nb.d = 2\n",
    "表の中の値を表にする": "[a.b]\n[a]\nb = 1\n",
    "値を表にする": "a = 1\n[a]\n",
    "並びに表の並びを足す": "a = [1]\n[[a]]\n",
    "表を表の並びにする": "[a]\n[[a]]\n",
    "行の中の並びの中に見出しで足す": "[[a]]\nb = [1]\n[a.b]\n",
    "文字列の中の改行": "a = \"x\ny\"\n",
    "文字列の中の制御の字": "a = \"x\x01\"\n",
    "読めない \\": "a = \"\\e\"\n",
    "字でない \\u": "a = \"\\uD800\"\n",
    "1 行に 2 つ": "a = 1 2\n",
    "見出しの後に鍵": "[a] b = 1\n",
    "前の 0": "a = 01\n",
    "点で始まる数": "a = .5\n",
    "引用符の無い文字": "a = bare\n",
    "値が無い": "a =\n",
    "鍵が無い": "= 1\n",
    "行の中の表の終わりの ,": "a = {b = 1,}\n",
    "行の中の表の改行": "a = {b = 1\n}\n",
    "並びの区切りが無い": "a = [1 2]\n",
    "閉じない並び": "a = [1,\n",
    "閉じない見出し": "[a\n",
    "注釈の中の制御の字": "a = 1 # x\x01\n",
    "BOM": "\ufeffa = 1\n",
    "改行でない CR": "a = 1\rb = 2\n",
}

#: tomllib は読むが、この読み手が読まない書き方（止める。複数行の文字列）
TOML_REFUSED = {
    "複数行の文字列": "a = \"\"\"x\ny\"\"\"\n",
    "複数行の字のままの文字列": "a = '''x'''\n",
}


def toml_shape(value):
    """tomllib の結果を、この読み手の形（文字列と表と並びのほかは SCALAR）にする。"""
    if isinstance(value, dict):
        return {k: toml_shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [toml_shape(v) for v in value]
    return value if isinstance(value, str) else IV.SCALAR


class TomlReader(unittest.TestCase):
    """根の pyproject.toml を読む、標準の部品だけの小さな TOML の読み手（read_toml）。"""

    def test_it_reads_what_it_should(self):
        got = IV.read_toml(TOML_GOOD["いろいろな書き方"])
        self.assertEqual(got["quoted key"], "literal \\ value")
        self.assertEqual(got["dotted"], {"key": {"a": IV.SCALAR, "b": IV.SCALAR}})
        self.assertEqual(got["site"], {"example.com": IV.SCALAR})
        self.assertEqual(got["a"]["b"], [IV.SCALAR, IV.SCALAR, [IV.SCALAR, "4"], {"x": IV.SCALAR}])
        self.assertEqual(got["a"]["c"], {"d": "e", "f": {"g": "h"}})
        self.assertEqual(got["a"]["esc"], "a\tb\u3042\U0001F600\\\"")
        self.assertEqual(got["a"]["jp"], "日本語")
        self.assertEqual(got["a"]["sub"], {"x": IV.SCALAR})
        self.assertEqual([e["inner"]["y"] for e in got["arr"]], [IV.SCALAR, IV.SCALAR])
        self.assertEqual(got["arr"][1]["v"], {"z": IV.SCALAR})
        self.assertEqual(got["project"]["entry-points"], {"mirumeai_pdm.x": {"a": "b"}})
        self.assertEqual(got["tool"]["mirumeai"]["install-set-extra"], ["docs", "plugins"])
        self.assertEqual(IV.read_toml(TOML_GOOD["改行が CRLF"]), {"project": {"name": "x"}})
        self.assertEqual(IV.read_toml(""), {})

    def test_deep_nesting_is_refused_as_a_reading_error(self):
        """深く入れ子にした並びは、RecursionError でなく読めない物（TomlError）として止める（宣言の読み手が Stop にまとめる）。"""
        with self.assertRaisesRegex(IV.TomlError, "入れ子が深すぎます"):
            IV.read_toml("a = " + "[" * 5000 + "]" * 5000 + "\n")

    def test_it_refuses_what_toml_refuses(self):
        for label, text in {**TOML_BAD, **TOML_REFUSED}.items():
            with self.subTest(label):
                with self.assertRaises(IV.TomlError) as cm:
                    IV.read_toml(text)
                self.assertRegex(str(cm.exception), r"^[0-9]+ 行目: ")

    @unittest.skipIf(tomllib is None, "tomllib の無い Python（3.10）。読み手の照らしは上の試験で流す")
    def test_it_reads_like_tomllib(self):
        """tomllib のある Python で、読める物は同じに読み、tomllib が断る物はこの読み手も断る（試験の例が本当に TOML の
        決まりのとおりかも照らす）。複数行の文字列は、tomllib は読み、この読み手は止める。暦に無い日付（値の中身）は、
        tomllib は断り、この読み手は読む（読む鍵に使わない。試験の例に入れない）。"""
        for label, text in TOML_GOOD.items():
            with self.subTest(good=label):
                self.assertEqual(IV.read_toml(text), toml_shape(tomllib.loads(text)))
        for label, text in TOML_BAD.items():
            with self.subTest(bad=label), self.assertRaises(tomllib.TOMLDecodeError):
                tomllib.loads(text)
        for label, text in TOML_REFUSED.items():
            with self.subTest(refused=label):
                tomllib.loads(text)
        if IN_CASE:
            text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
            self.assertEqual(IV.read_toml(text), toml_shape(tomllib.loads(text)))


class CoreRanges(unittest.TestCase):
    """入れた後に、その venv の製品のパッケージの命令 `doctor` で Core の版の範囲を照らす（範囲の表は製品のパッケージだけが
    持ち、この道具は写さない。Python の名前は根の pyproject.toml の [project].name から作る）。"""

    def test_verify_runs_doctor_and_stops_when_it_fails(self):
        for judge in (False, True):
            for fails in (False, True):
                with self.subTest(judge=judge, fails=fails):
                    calls = []

                    def run(cmd, capture=False, env=None):
                        calls.append([str(c) for c in cmd])
                        if "doctor" in cmd and fails:
                            raise IV.Stop("落ちました: py -I -m fake_case doctor …（NG: mirumeai-pdm 0.10.0 は、範囲の外）")
                        return "[]" if capture else ""

                    bad = IV.verify(run, Path("py"), {"pins": [], "seed": [], "extras": [], "judge": judge,
                                                      "package": "fake_case"})
                    doctor = [c for c in calls if "doctor" in c]
                    self.assertEqual(doctor, [["py", "-I", "-m", "fake_case", "doctor"] + (["--judge-venv"] if judge else [])])
                    if fails:
                        self.assertTrue(any("組める範囲に入りません（python -m fake_case doctor）" in b and "0.10.0" in b
                                            for b in bad), bad)
                    else:
                        self.assertEqual(bad, [])


def sys_executable():
    import sys
    return sys.executable


#: 偽の組み立ての道具の wheel（ファイルの名前 → 中身）。wheel 0.48.0 の依存 packaging も書く
BUILD_WHEELS = {"setuptools-84.2.1-py3-none-any.whl": b"setuptools", "wheel-0.48.0-py3-none-any.whl": b"wheel",
                "packaging-26.4-py3-none-any.whl": b"packaging"}
#: 束を作る試験の偽の案件の根の pyproject.toml（組み立ての道具の版と、その wheel の sha256）
DEMO_PYPROJECT = ('[build-system]\nrequires = ["setuptools==84.2.1", "wheel==0.48.0"]\n'
                  'build-backend = "setuptools.build_meta"\n\n[project]\nname = "demo-pkg"\n\n'
                  '[tool.mirumeai.build-wheels]\n'
                  + "".join(f'"{f}" = "{hashlib.sha256(b).hexdigest()}"\n' for f, b in BUILD_WHEELS.items())).encode()


class MakeProductBundle(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = self.tmp / "repo"
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_CEILING_DIRECTORIES=str(self.tmp),
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        self.env = env
        self.wheel_runs = []
        self.git_runs = []
        (self.repo / "src" / "demo_pkg").mkdir(parents=True)
        (self.repo / "pyproject.toml").write_bytes(DEMO_PYPROJECT)
        (self.repo / "src" / "demo_pkg" / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
        self.find_links = []
        for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "1"],
                    ["update-ref", "refs/remotes/origin/main", "HEAD"]):           # main に入った commit
            self.git(*cmd)

    def git(self, *cmd):
        return subprocess.run(["git", "-C", str(self.repo), *cmd], env=self.env, check=True, capture_output=True,
                              text=True).stdout

    def fake_run(self, cmd, **kw):
        """git の命令は、道具が渡した環境のまま本物で流し（環境を記録する）、wheel を作る命令だけを偽物にする（決まった名前の
        wheel を置く）。"""
        if cmd[:1] == ["git"]:
            self.git_runs.append((cmd, kw.get("env")))
            return subprocess.run(cmd, **kw)
        self.wheel_runs.append((cmd, kw.get("env")))
        if cmd[1:4] == ["-I", "-m", "venv"]:
            (Path(cmd[4]) / "bin").mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if "--find-links" in cmd:                      # 組み立ての道具を入れる命令（そのときの置き場と要求を記録する）
            links = Path(cmd[cmd.index("--find-links") + 1])
            self.find_links.append(({f.name: f.read_bytes() for f in links.iterdir()},
                                    Path(cmd[cmd.index("-r") + 1]).read_text(encoding="utf-8")))
        if "--wheel-dir" not in cmd:                   # 組み立ての道具を入れる・照らす命令
            return subprocess.CompletedProcess(cmd, 0, "", "")
        out = Path(cmd[cmd.index("--wheel-dir") + 1])
        out.mkdir(parents=True, exist_ok=True)
        self.assertTrue((Path(cmd[-1]) / "pyproject.toml").is_file())  # 写した木から作る
        (out / "demo_pkg-0.2.0-py3-none-any.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def place_build_wheels(self, out):
        """組み立ての道具の置き場（束の置き場の build-wheels/）に、宣言した wheel を置く。"""
        store = out / PB.BUILD_WHEELS
        if not store.exists():
            store.mkdir(parents=True)
            for f, body in BUILD_WHEELS.items():
                (store / f).write_bytes(body)
        return store

    def make(self, out, run=None):
        self.place_build_wheels(out)
        with mock.patch.dict(os.environ, self.env, clear=True):
            return PB.make(self.repo, out, "python3.10", run=run or self.fake_run)

    def test_the_bundle_is_written_and_read_only(self):
        out = self.tmp / "wheelhouse"
        release = self.make(out)
        bundle = out / "demo-pkg" / "0.2.0"
        self.assertEqual(release["wheel_sha256"], sha(b"wheel"))
        self.assertEqual(release["sha256sums_sha256"], sha((bundle / "SHA256SUMS").read_bytes()))
        self.assertEqual((bundle / "SHA256SUMS").read_text(encoding="utf-8"),
                         f"{sha(b'wheel')}  wheelhouse/demo_pkg-0.2.0-py3-none-any.whl\n")
        self.assertEqual(json.loads((bundle / "release.json").read_text(encoding="utf-8"))["product"], "demo-pkg")
        self.assertFalse(os.access(bundle / "SHA256SUMS", os.W_OK))
        # 入れる道具の照らしに、そのまま通る
        IV.check_bundle(bundle, sha256sums_sha256=None, wheel_sha256=release["wheel_sha256"], label="製品",
                        product={"name": "demo-pkg", "version": "0.2.0"})

    def test_the_wheel_is_built_isolated_with_a_narrowed_environment(self):
        """pip wheel は -I で流し、入れる道具の pip_env と同じ形の環境（PIP_・_PIP_・PYTHON で始まる名前を外し、pip の設定の
        ファイルを読まない）を渡す。"""
        self.env.update(PIP_INDEX_URL="http://example.invalid/simple", PIP_CONFIG_FILE=str(self.tmp / "pip.conf"),
                        _PIP_STANDALONE_CERT=str(self.tmp / "ca.pem"), PYTHONPATH=str(self.tmp / "elsewhere"),
                        PYTHONHOME=str(self.tmp))
        self.env.update(SETUPTOOLS_USE_DISTUTILS="stdlib", DISTUTILS_DEBUG="1", DIST_EXTRA_CONFIG=str(self.tmp / "x.cfg"),
                        HOME=str(self.tmp / "real-home"))
        self.make(self.tmp / "wheelhouse")
        self.assertEqual([c[1:4] for c, _ in self.wheel_runs], [["-I", "-m", "venv"]] + [["-I", "-m", "pip"]] * 3)
        self.assertEqual([c[4] for c, _ in self.wheel_runs[1:]], ["install", "check", "wheel"])
        venv_python = str(Path(self.wheel_runs[0][0][4]) / "bin" / "python")
        self.assertEqual({c[0] for c, _ in self.wheel_runs[1:]}, {venv_python})   # 入れる・照らす・作るのは使い捨ての venv
        wheel_cmd = self.wheel_runs[-1][0]
        for flag in ("--no-deps", "--no-index", "--no-build-isolation", "--check-build-dependencies"):
            self.assertIn(flag, wheel_cmd)
        for cmd, env in self.wheel_runs:
            with self.subTest(cmd=cmd[1:5]):
                self.assertIsNotNone(env)
                self.assertEqual([k for k in env if k.startswith(("PYTHON", "_PIP_"))], ["PYTHONNOUSERSITE"])
                self.assertEqual(env["PYTHONNOUSERSITE"], "1")
                self.assertEqual({k: v for k, v in env.items() if k.startswith("PIP_")},
                                 {"PIP_CONFIG_FILE": os.devnull, "PIP_NO_INPUT": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
                # setuptools・distutils の利用者の設定を読ませない（HOME は空の一時の場所）
                self.assertFalse([k for k in env if k.startswith(("SETUPTOOLS_", "DISTUTILS_")) or k == "DIST_EXTRA_CONFIG"])
                self.assertNotEqual(env["HOME"], str(self.tmp / "real-home"))
                self.assertTrue(env["HOME"].startswith(tempfile.gettempdir()))
                self.assertEqual(env.get("GIT_CONFIG_NOSYSTEM"), "1")       # ほかの名前は残す

    def test_the_build_tools_are_installed_by_hash_from_a_checked_copy(self):
        """組み立ての道具は、宣言した wheel だけを置き場から写して sha256 を照らした所から、index なし・hash で入れる（置き場の
        ほかの物は使わない）。依存の wheel も宣言に書き、入れた後に pip check で照らす。"""
        store = self.place_build_wheels(self.tmp / "wheelhouse")
        (store / "evil-1.0-py3-none-any.whl").write_bytes(b"evil")
        self.make(self.tmp / "wheelhouse")
        install = self.wheel_runs[1][0]
        for flag in ("--no-index", "--no-deps", "--require-hashes"):
            self.assertIn(flag, install)
        self.assertEqual(install[install.index("--only-binary") + 1], ":all:")
        self.assertNotEqual(Path(install[install.index("--find-links") + 1]), store)
        (links, req), = self.find_links
        self.assertEqual(links, BUILD_WHEELS)
        self.assertEqual(sorted(req.splitlines()),
                         sorted(f"{n}=={v} --hash=sha256:{hashlib.sha256(BUILD_WHEELS[f]).hexdigest()}"
                                for f, (n, v) in {"setuptools-84.2.1-py3-none-any.whl": ("setuptools", "84.2.1"),
                                                  "wheel-0.48.0-py3-none-any.whl": ("wheel", "0.48.0"),
                                                  "packaging-26.4-py3-none-any.whl": ("packaging", "26.4")}.items()))

    def test_build_tools_that_are_missing_changed_or_linked_are_refused(self):
        out = self.tmp / "wheelhouse"
        store = self.place_build_wheels(out)
        cases = [("無い", lambda f: f.unlink(), "組み立ての道具の置き場に wheel-0.48.0-py3-none-any.whl がありません"),
                 ("変わった", lambda f: f.write_bytes(b"other"), "wheel-0.48.0-py3-none-any.whl の sha256 が"),
                 ("リンク", lambda f: (f.unlink(), f.symlink_to(self.tmp / "elsewhere.whl")), "がありません（リンクは使わない）")]
        (self.tmp / "elsewhere.whl").write_bytes(b"wheel")
        for label, change, says in cases:
            with self.subTest(label):
                f = store / "wheel-0.48.0-py3-none-any.whl"
                change(f)
                with self.assertRaisesRegex(PB.Stop, re.escape(says)):
                    self.make(out)
                self.assertFalse((out / "demo-pkg").exists())
                self.assertEqual(self.wheel_runs, [])                   # 何も入れない
                if f.is_symlink():
                    f.unlink()
                f.write_bytes(b"wheel")
        shutil.rmtree(store)
        store.mkdir()
        shutil.rmtree(store)
        store.symlink_to(self.tmp)
        with self.assertRaisesRegex(PB.Stop, "組み立ての道具の置き場がありません"):
            with mock.patch.dict(os.environ, self.env, clear=True):
                PB.make(self.repo, out, "python3.10", run=self.fake_run)

    def test_the_build_declaration_must_pin_every_build_tool(self):
        """[build-system].requires は、どれも版を固定した `名前==版` で、[tool.mirumeai.build-wheels] にその名前と版の wheel が
        1 つずつある（依存の wheel は足してよいが、名前ごとに 1 つ）。"""
        h = "a" * 64
        top = self.tmp / "decl"
        top.mkdir()

        def plan(requires, wheels):
            (top / "pyproject.toml").write_text(
                f"[build-system]\nrequires = {json.dumps(requires)}\n[project]\nname = \"x\"\n[tool.mirumeai.build-wheels]\n"
                + "".join(f'"{w}" = "{h}"\n' for w in wheels), encoding="utf-8")
            return PB.build_plan(top)

        good = ["setuptools-84.2.1-py3-none-any.whl", "wheel-0.48.0-py3-none-any.whl"]
        self.assertEqual(plan(["setuptools==84.2.1", "wheel==0.48.0"], good),
                         {good[0]: ("setuptools", "84.2.1", h), good[1]: ("wheel", "0.48.0", h)})
        self.assertEqual(len(plan(["setuptools==84.2.1"], good)), 2)            # 依存の wheel は足してよい
        cases = {"範囲": (["setuptools>=61"], good, "版を固定した `名前==版` ではありません"),
                 "印": (["setuptools==84.2.1; python_version>'3'"], good, "版を固定した"),
                 "同じ名前を 2 度": (["setuptools==84.2.1", "Setuptools==84.2.1"], good, "版を固定した"),
                 "要求が無い": ([], good, "[build-system].requires がありません"),
                 "wheel が無い": (["setuptools==84.2.1", "wheel==0.48.0"], good[:1], "wheel==0.48.0 の wheel と sha256 がありません"),
                 "版が違う": (["setuptools==84.2.1"], ["setuptools-84.2.0-py3-none-any.whl"], "setuptools==84.2.1 の wheel"),
                 "同じ名前の wheel が 2 つ": (["setuptools==84.2.1"], good[:1] + ["setuptools-84.2.0-py3-none-any.whl"],
                                              "setuptools の wheel が 2 つあります")}
        for label, (requires, wheels, says) in cases.items():
            with self.subTest(label):
                with self.assertRaisesRegex(PB.Stop, re.escape(says)):
                    plan(requires, wheels)

    def test_build_tools_whose_dependencies_are_missing_stop(self):
        """組み立ての道具の依存が足りないとき（pip check が落ちる）は止め、作りかけの束を残さない。"""
        def no_check(cmd, **kw):
            if cmd[1:5] == ["-I", "-m", "pip", "check"]:
                return subprocess.CompletedProcess(cmd, 1, "wheel 0.48.0 requires packaging, which is not installed.", "")
            return self.fake_run(cmd, **kw)
        with self.assertRaisesRegex(PB.Stop, "組み立ての道具の依存が足りないか合いません"):
            self.make(self.tmp / "wheelhouse", run=no_check)
        self.assertFalse((self.tmp / "wheelhouse" / "demo-pkg" / "0.2.0").exists())
        self.assertFalse([c for c, _ in self.wheel_runs if "--wheel-dir" in c])

    def test_an_existing_bundle_is_not_rewritten(self):
        out = self.tmp / "wheelhouse"
        self.make(out)
        with self.assertRaisesRegex(PB.Stop, "同じ版の束がもうあります"):
            self.make(out)

    def test_a_commit_not_in_origin_main_is_refused(self):
        """main に入っていない commit（merge の前の Branch）からは作らない。origin/main が無いときも。"""
        self.git("checkout", "-q", "-b", "feature/x")
        (self.repo / "src" / "demo_pkg" / "__init__.py").write_text('__version__ = "0.2.0"\nX = 1\n', encoding="utf-8")
        self.git("commit", "-q", "-am", "2")
        with self.assertRaisesRegex(PB.Stop, "origin/main に入っていません"):
            self.make(self.tmp / "wheelhouse")
        self.assertFalse((self.tmp / "wheelhouse" / "demo-pkg").exists())
        # `origin/main` という名前のローカルの Branch は読まない（追跡の ref を名指しする）
        self.git("branch", "origin/main", "feature/x")
        with self.assertRaisesRegex(PB.Stop, "origin/main に入っていません"):
            self.make(self.tmp / "wheelhouse")
        self.git("branch", "-D", "origin/main")
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.git("checkout", "-q", "main")
        with self.assertRaisesRegex(PB.Stop, "origin/main に入っていません"):
            self.make(self.tmp / "wheelhouse")
        self.git("update-ref", "refs/remotes/origin/main", "feature/x")          # Branch が merge された後の main
        self.assertEqual(self.make(self.tmp / "wheelhouse")["commit"], self.git("rev-parse", "HEAD").strip())

    def test_uncommitted_changes_are_refused(self):
        (self.repo / "src" / "demo_pkg" / "__init__.py").write_text('__version__ = "0.2.0"\nX = 1\n', encoding="utf-8")
        with self.assertRaisesRegex(PB.Stop, "commit していない変更"):
            self.make(self.tmp / "wheelhouse")
        self.assertFalse((self.tmp / "wheelhouse" / "demo-pkg").exists())


    def commit_extra(self):
        """偽の Repository に、ふつうのファイル・実行できるファイル・入れ子のファイルを足して commit する。"""
        (self.repo / "extra.txt").write_text("commit の中身\n", encoding="utf-8")
        (self.repo / "tools").mkdir()
        (self.repo / "tools" / "run.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
        (self.repo / "tools" / "run.sh").chmod(0o755)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "2")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        return self.git("rev-parse", "HEAD").strip()

    def copied(self, commit):
        dest = self.tmp / f"copy-{len(list(self.tmp.glob('copy-*')))}"
        dest.mkdir()
        n = PB.copy_tree(self.repo, commit, dest)
        files = {p.relative_to(dest).as_posix(): p for p in dest.rglob("*") if p.is_file()}
        self.assertEqual(n, len(files))
        return files

    def test_the_copied_tree_is_the_commit_whatever_the_local_git_state(self):
        """手元の属性の export-ignore と filter・Repository の設定の filter・置き換えの ref・環境の GIT_CONFIG_COUNT が
        あっても、写すのは commit の blob そのまま（git archive はこれらに従う）。"""
        commit = self.commit_extra()
        want = {"pyproject.toml": DEMO_PYPROJECT, "src/demo_pkg/__init__.py": b'__version__ = "0.2.0"\n',
                "extra.txt": "commit の中身\n".encode(), "tools/run.sh": b"#!/bin/sh\necho ok\n"}
        info = self.repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "attributes").write_text("extra.txt export-ignore\n*.toml filter=evil\n", encoding="utf-8")
        self.git("config", "filter.evil.smudge", "sed s/demo/evil/")
        fake = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input="置き換え\n",
                              env=self.env, capture_output=True, text=True, check=True).stdout.strip()
        blob = self.git("rev-parse", f"{commit}:src/demo_pkg/__init__.py").strip()
        self.git("replace", blob, fake)
        outer = dict(self.env, GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="filter.env.smudge", GIT_CONFIG_VALUE_0="sed s/ok/evil/")
        (info / "attributes").write_text("extra.txt export-ignore\n*.toml filter=evil\n*.sh filter=env\n", encoding="utf-8")
        with mock.patch.dict(os.environ, outer, clear=True):
            files = self.copied(commit)
        self.assertEqual({k: v.read_bytes() for k, v in files.items()}, want)
        self.assertTrue(os.access(files["tools/run.sh"], os.X_OK))
        self.assertFalse(os.access(files["extra.txt"], os.X_OK))
        # 照らし: 同じ手元の状態で、git archive は commit と違う中身を写す（この試験が手元の状態を本当に効かせている）
        with mock.patch.dict(os.environ, outer, clear=True):
            tar = subprocess.run(["git", "-C", str(self.repo), "archive", "--format=tar", commit], capture_output=True)
        import io
        import tarfile
        with tarfile.open(fileobj=io.BytesIO(tar.stdout)) as t:
            names = t.getnames()
            init = t.extractfile("src/demo_pkg/__init__.py").read()
            toml = t.extractfile("pyproject.toml").read()
            script = t.extractfile("tools/run.sh").read()
        self.assertNotIn("extra.txt", names)                               # 手元の属性の export-ignore
        self.assertNotEqual(init, want["src/demo_pkg/__init__.py"])        # 置き換えの ref
        self.assertNotEqual(toml, want["pyproject.toml"])                  # Repository の設定の filter
        self.assertNotEqual(script, want["tools/run.sh"])                  # 環境の GIT_CONFIG_COUNT の filter

    def test_a_blob_whose_content_differs_from_its_name_stops(self):
        """cat-file が返した中身の hash が blob の名前と違えば止める。"""
        commit = self.commit_extra()

        def tampered(cmd, **kw):
            r = subprocess.run(cmd, **kw)
            if "cat-file" in cmd:
                r = subprocess.CompletedProcess(cmd, r.returncode, r.stdout.replace("commit の中身".encode(), "別の中身だよ".encode()),
                                                r.stderr)
            return r
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "blob の中身が名前の hash と違います"):
            PB.copy_tree(self.repo, commit, dest, run=tampered)

    def test_links_and_submodules_in_the_commit_are_refused(self):
        os.symlink("extra.txt", self.repo / "link.txt")
        (self.repo / "extra.txt").write_text("x\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "link")
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "リンクか外を指す道"):
            PB.copy_tree(self.repo, self.git("rev-parse", "HEAD").strip(), dest)
        self.git("rm", "-q", "link.txt")
        self.git("update-index", "--add", "--cacheinfo", f"160000,{self.git('rev-parse', 'HEAD').strip()},sub")
        self.git("commit", "-q", "-m", "sub")
        with self.assertRaisesRegex(PB.Stop, "リンクか外を指す道"):
            PB.copy_tree(self.repo, self.git("rev-parse", "HEAD").strip(), dest)

    def literal_tree(self, entries):
        """git が普通は作らない木（`..` の名前・同じ名前の 2 度）を作り、その木を持つ commit を返す。"""
        blob = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"], input=b"x\n", env=self.env,
                              capture_output=True, check=True).stdout.decode().strip()
        raw = b"".join(b"100644 " + n + b"\0" + bytes.fromhex(blob) for n in entries)
        tree = subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "-t", "tree", "--literally", "--stdin"],
                              input=raw, env=self.env, capture_output=True, check=True).stdout.decode().strip()
        return subprocess.run(["git", "-C", str(self.repo), "commit-tree", tree, "-m", "t"], env=self.env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_names_outside_the_tree_and_repeated_names_are_refused(self):
        """木の名前に `..` があれば外を指す道として、同じ名前が 2 度あれば止める文で止める。"""
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "リンクか外を指す道"):
            PB.copy_tree(self.repo, self.literal_tree([b".."]), dest)
        with self.assertRaisesRegex(PB.Stop, "同じ名前が 2 度あります"):
            PB.copy_tree(self.repo, self.literal_tree([b"a.txt", b"a.txt"]), dest)

    def raw_object(self, kind, body):
        return subprocess.run(["git", "-C", str(self.repo), "hash-object", "-w", "-t", kind, "--literally", "--stdin"],
                              input=body, env=self.env, capture_output=True, check=True).stdout.decode().strip()

    def raw_commit(self, entries):
        """(mode, 名前, object) の並びの根の木を持つ commit（git が普通は作らない木も作れる）。"""
        tree = self.raw_object("tree", b"".join(m + b" " + n + b"\0" + bytes.fromhex(o) for m, n, o in entries))
        return subprocess.run(["git", "-C", str(self.repo), "commit-tree", tree, "-m", "t"], env=self.env, capture_output=True,
                              text=True, check=True).stdout.strip()

    def test_a_replaced_subtree_object_stops(self):
        """object の置き場で部分木の object を、名前の同じ違う中身に差し替えると止める（木の hash も照らす）。"""
        import zlib
        commit = self.commit_extra()
        sub = self.git("rev-parse", f"{commit}:tools").strip()
        other = self.raw_object("blob", b"#!/bin/sh\necho evil\n")
        evil = b"100755 run.sh\0" + bytes.fromhex(other)
        loose = self.repo / ".git" / "objects" / sub[:2] / sub[2:]
        loose.chmod(0o644)
        loose.write_bytes(zlib.compress(b"tree %d\0" % len(evil) + evil))
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "tree の中身が名前の hash と違います: tools/"):
            PB.copy_tree(self.repo, commit, dest)

    def test_a_file_and_a_folder_of_the_same_name_stop_in_either_order(self):
        """同じ木の中で、ファイルの後に同じ名前のフォルダが来ても、その逆でも、止める文で止める。"""
        blob = self.raw_object("blob", b"x\n")
        sub = self.raw_object("tree", b"100644 b.txt\0" + bytes.fromhex(blob))
        for entries in ([(b"100644", b"a", blob), (b"40000", b"a", sub)], [(b"40000", b"a", sub), (b"100644", b"a", blob)]):
            dest = self.tmp / f"copy-{entries[0][0].decode()}"
            dest.mkdir()
            with self.assertRaisesRegex(PB.Stop, "同じ名前が 2 度あります: a"):
                PB.copy_tree(self.repo, self.raw_commit(entries), dest)

    def test_a_repeated_name_stops_while_reading_the_tree_before_writing(self):
        """同じ名前は木を読む所で止め、何も書かない（書く所の捕まえだけに頼らない）。"""
        blob = self.raw_object("blob", b"x\n")
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "同じ名前が 2 度あります: a.txt"):
            PB.copy_tree(self.repo, self.raw_commit([(b"100644", b"a.txt", blob), (b"100644", b"a.txt", blob)]), dest)
        self.assertEqual(list(dest.iterdir()), [])

    def test_git_dir_names_are_refused(self):
        """木の中の .git という名前は、大文字と小文字を問わず断る（git と同じ。一式で機体に置かないように）。"""
        blob = self.raw_object("blob", b"x\n")
        for name in (b".git", b".GIT", b".Git"):
            sub = self.raw_object("tree", b"100644 config\0" + bytes.fromhex(blob))
            dest = self.tmp / f"copy-{name.decode()}"
            dest.mkdir()
            with self.subTest(name=name), self.assertRaisesRegex(PB.Stop, "リンクか外を指す道"):
                PB.copy_tree(self.repo, self.raw_commit([(b"40000", b"deploy", self.raw_object(
                    "tree", b"40000 " + name + b"\0" + bytes.fromhex(sub)))]), dest)

    def test_an_old_group_writable_mode_is_copied_as_a_plain_file(self):
        """古い 100664 の mode は 100644 と同じに 0644 で写す（git と同じ）。"""
        blob = self.raw_object("blob", b"x\n")
        dest = self.tmp / "copy"
        dest.mkdir()
        self.assertEqual(PB.copy_tree(self.repo, self.raw_commit([(b"100664", b"a.txt", blob)]), dest), 1)
        self.assertEqual(stat.S_IMODE((dest / "a.txt").stat().st_mode), 0o644)

    def test_a_commit_object_whose_content_differs_from_its_name_stops(self):
        """cat-file が返した commit の中身の hash が名前と違えば止める。"""
        commit = self.commit_extra()

        def tampered(cmd, **kw):
            r = subprocess.run(cmd, **kw)
            if "cat-file" in cmd and kw.get("input", b"").strip() == commit.encode():
                r = subprocess.CompletedProcess(cmd, r.returncode, r.stdout.replace(b"\n2\n", b"\n3\n"), r.stderr)
            return r
        dest = self.tmp / "copy"
        dest.mkdir()
        with self.assertRaisesRegex(PB.Stop, "commit の中身が名前の hash と違います"):
            PB.copy_tree(self.repo, commit, dest, run=tampered)

    def test_a_sha256_repository_is_copied(self):
        """object の形が sha256 の Repository でも写せる（木の中の hash の幅が 32 バイト）。"""
        repo = self.tmp / "repo256"
        if subprocess.run(["git", "init", "-q", "--object-format=sha256", str(repo)], env=self.env,
                          capture_output=True).returncode != 0:
            self.skipTest("この git は sha256 の Repository を作れない")
        (repo / "d").mkdir()
        (repo / "d" / "a.txt").write_text("a\n", encoding="utf-8")
        (repo / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        (repo / "run.sh").chmod(0o755)
        for cmd in (["add", "-A"], ["commit", "-q", "-m", "1"]):
            subprocess.run(["git", "-C", str(repo), *cmd], env=self.env, check=True, capture_output=True)
        dest = self.tmp / "copy256"
        dest.mkdir()
        self.assertEqual(PB.copy_tree(repo, "HEAD", dest), 2)
        self.assertEqual((dest / "d" / "a.txt").read_text(encoding="utf-8"), "a\n")
        self.assertEqual(stat.S_IMODE((dest / "run.sh").stat().st_mode), 0o755)

    def test_a_bundle_is_not_left_when_the_wheel_cannot_be_made(self):
        """wheel を作れないときは、作りかけの束を残さない。"""
        def no_wheel(cmd, **kw):
            if "--wheel-dir" not in cmd:
                return self.fake_run(cmd, **kw)
            return subprocess.CompletedProcess(cmd, 1, "", "壊れた")
        with self.assertRaisesRegex(PB.Stop, "wheel を作れません"):
            self.make(self.tmp / "wheelhouse", run=no_wheel)
        self.assertFalse((self.tmp / "wheelhouse" / "demo-pkg" / "0.2.0").exists())

    def test_names_outside_top_are_skipped_before_they_are_read(self):
        """top の外の名前は、読む前にバイトで照らして飛ばす（読めない名前があっても止めない）。"""
        blob = self.raw_object("blob", b"x\n")
        commit = self.raw_commit([(b"100644", b"keep.txt", blob), (b"100644", b"\xff\xfe", blob)])
        dest = self.tmp / "copy"
        dest.mkdir()
        self.assertEqual(PB.copy_tree(self.repo, commit, dest, top=("keep.txt",)), 1)
        self.assertEqual([p.name for p in dest.iterdir()], ["keep.txt"])
        dest2 = self.tmp / "copy2"
        dest2.mkdir()
        with self.assertRaisesRegex(PB.Stop, "読めない名前"):
            PB.copy_tree(self.repo, commit, dest2)

    def test_the_bundle_name_and_version_are_read_from_the_commit(self):
        """束の名前と版は写した commit の木から読み、作業ツリーと違えば止める（status に出ない違いも）。"""
        init = self.repo / "src" / "demo_pkg" / "__init__.py"
        self.git("update-index", "--assume-unchanged", "src/demo_pkg/__init__.py")
        init.write_text('__version__ = "0.3.0"\n', encoding="utf-8")
        with self.assertRaisesRegex(PB.Stop, "作業ツリーと commit で、製品の名前か版が違います（commit は demo-pkg 0.2.0）"):
            self.make(self.tmp / "wheelhouse")
        self.assertFalse((self.tmp / "wheelhouse" / "demo-pkg").exists())

    def test_names_and_versions_that_could_leave_the_bundle_folder_are_refused(self):
        """束の道に使う名前と版は、字を狭めて / と .. を断る。"""
        top = self.tmp / "decl"
        (top / "src" / "x").mkdir(parents=True)
        for name, version in (("../x", "0.1.0"), ("x", "../../0"), ("x", ".."), ("x/y", "0.1.0"), ("x", "1/2")):
            (top / "pyproject.toml").write_text(f'[project]\nname = "{name}"\n', encoding="utf-8")
            pkg = top / "src" / name.replace("-", "_")
            if ".." not in name and "/" not in name:
                (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
            with self.subTest(name=name, version=version), self.assertRaisesRegex(PB.Stop, "使えない字"):
                PB.declared(top)
        (top / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")
        (top / "src" / "x" / "__init__.py").write_text('__version__ = "0.1.0.dev0+local"\n', encoding="utf-8")
        self.assertEqual(PB.declared(top), ("x", "0.1.0.dev0+local"))

    def test_every_git_command_of_the_tool_gets_the_narrowed_environment(self):
        """道具が流すどの git（状態・rev-parse・merge-base・cat-file）にも絞った環境を渡す。外に GIT_DIR・
        GIT_INDEX_FILE・GIT_CONFIG_COUNT を置いても、束は commit から作れる。"""
        self.env.update(GIT_DIR=str(self.tmp / "elsewhere"), GIT_INDEX_FILE=str(self.tmp / "no-index"), GIT_CONFIG_COUNT="1",
                        GIT_CONFIG_KEY_0="core.bare", GIT_CONFIG_VALUE_0="true")
        release = self.make(self.tmp / "wheelhouse")
        self.assertEqual(release["version"], "0.2.0")
        self.assertTrue(self.git_runs)
        for cmd, env in self.git_runs:
            self.assertIsNotNone(env, cmd)
            self.assertEqual(env.get("GIT_NO_REPLACE_OBJECTS"), "1", cmd)
            self.assertFalse({"GIT_DIR", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT"} & set(env), cmd)

    def test_git_gets_a_narrowed_environment(self):
        """この道具が流す git には、GIT_ で始まる名前を外し、全体と利用者の設定を読まず、置き換えの ref を使わない環境を渡す。"""
        with mock.patch.dict(os.environ, {"GIT_CONFIG_COUNT": "1", "GIT_DIR": "/elsewhere", "GIT_CONFIG_GLOBAL": "/my.cfg",
                                          "HOME": "/home/u"}, clear=True):
            env = PB.git_env()
        self.assertEqual({k: v for k, v in env.items() if k.startswith("GIT_")},
                         {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_NO_REPLACE_OBJECTS": "1",
                          "GIT_GRAFT_FILE": os.devnull, "GIT_TERMINAL_PROMPT": "0"})
        self.assertEqual(env["HOME"], "/home/u")


if __name__ == "__main__":
    unittest.main()

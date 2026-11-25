"""命令 `doctor` と、 組める Core の版の範囲の照らし（`src/case_package/cores.py`）の試験。

Core を使わない（偽の dist-info だけ）。 PyYAML が要る（製品のパッケージの依存。 検査の `product` の job が入れる）。
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    import yaml
except ImportError:          # 無ければ、 下の試験が落ちる（飛ばさない）
    yaml = None

from case_package import cli, cores      # noqa: E402

IA, PDM = cores.DIST_IA, cores.DIST_PDM
#: 試験だけで使う範囲（表の値ではない）
TEST_RANGES = {IA: ("0.6.0", "0.7"), PDM: ("0.7.0", "0.8")}


def write_dist(site: Path, name: str, version: str, dirname: "str | None" = None) -> Path:
    d = site / (dirname or f"{name.replace('-', '_')}-{version}.dist-info")
    d.mkdir(parents=True)
    (d / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n\n本文\nVersion: 9\n",
                                encoding="utf-8")
    return d


class RangeTest(unittest.TestCase):
    """比べ方は PEP 440（`>=L` は L の開発の版と前の版を入れない。 `<H` は H の開発の版と前の版を入れない）。"""

    def test_the_ends_and_the_versions_between(self) -> None:
        cases = {
            "0.6.0": True, "0.6": True, "0.6.1": True, "0.6.1.dev0": True, "0.6.1rc1": True, "0.6.9.post1": True,
            "0.6.0+local.1": True, "0.5.9": False, "0.6.0.dev0": False, "0.6.0rc1": False, "0.7": False,
            "0.7.0": False, "0.7.0.dev0": False, "0.7.0a1": False, "0.7.0.post1": False, "0.70.0": False,
        }
        for version, inside in cases.items():
            with self.subTest(version=version):
                self.assertIs(cores.in_range(version, "0.6.0", "0.7"), inside)

    def test_unknown_forms_and_unstable_ends_stop(self) -> None:
        for version in ("", "v0.6.0", "0.6.0-dev0", "0.6.0.dev", "1!0.6.0", "0.6.x", "0.6.0+Local", "abc"):
            with self.subTest(version=version), self.assertRaises(cores.CoreVersionError):
                cores.in_range(version, "0.6.0", "0.7")
        for low, high in (("0.6.0.dev0", "0.7"), ("0.6.0", "0.7rc1")):
            with self.subTest(low=low, high=high), self.assertRaises(cores.CoreVersionError):
                cores.in_range("0.6.1", low, high)


class DoctorTest(unittest.TestCase):

    def setUp(self) -> None:
        self.assertIsNotNone(yaml, "PyYAML がありません（製品のパッケージの依存）")
        self.site = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.site, True)

    def doctor(self, judge: bool = False, ranges: "dict | None" = None) -> "tuple[int, str]":
        out = io.StringIO()
        with mock.patch.dict(cores.RANGES, ranges or TEST_RANGES), redirect_stdout(out), redirect_stderr(out):
            code = cli.doctor(judge_venv=judge, site_packages=self.site)
        return code, out.getvalue()

    def test_inside_the_ranges_ends_with_0(self) -> None:
        write_dist(self.site, IA, "0.6.1")
        code, out = self.doctor()
        self.assertEqual(code, 0, out)
        self.assertIn(f"{IA} 0.6.1（範囲 >=0.6.0,<0.7）", out)
        self.assertIn(f"{PDM}: この環境には入っていません", out)
        write_dist(self.site, PDM, "0.7.0")
        code, out = self.doctor(judge=True)
        self.assertEqual(code, 0, out)

    def test_it_stops_outside_missing_or_unselected(self) -> None:
        code, out = self.doctor()
        self.assertEqual(code, 1, out)
        self.assertIn(f"NG: {IA} が入っていません", out)
        write_dist(self.site, IA, "0.7.0.dev0")
        code, out = self.doctor()
        self.assertEqual(code, 1, out)
        self.assertIn("組める範囲（>=0.6.0,<0.7）の外", out)
        write_dist(self.site, PDM, "0.7.0")
        write_dist(self.site, PDM, "0.7.1", dirname="Mirumeai.PDM-0.7.1.dist-info")
        self.assertIn("2 つあります", self.doctor()[1])          # --judge-venv が無くても照らす
        code, out = self.doctor(ranges={IA: None, PDM: None})
        self.assertEqual(code, 1, out)
        self.assertIn("範囲が未選択", out)

    def test_judge_venv_needs_pdm(self) -> None:
        write_dist(self.site, IA, "0.6.0")
        code, out = self.doctor(judge=True)
        self.assertEqual(code, 1, out)
        self.assertIn(f"NG: {PDM} が入っていません", out)

    def test_it_needs_pyyaml_6(self) -> None:
        write_dist(self.site, IA, "0.6.0")
        for ver in (None, "5.4.1"):
            with self.subTest(ver=ver), mock.patch.object(cli, "_yaml_version", return_value=ver):
                code, out = self.doctor()
                self.assertEqual(code, 1, out)
                self.assertIn("PyYAML の 6 の系列が要ります", out)

    def test_the_command(self) -> None:
        with mock.patch.object(cli, "doctor", return_value=0) as d:
            self.assertEqual(cli.main(["doctor"]), 0)
            self.assertEqual(cli.main(["doctor", "--judge-venv"]), 0)
        self.assertEqual(d.call_args_list, [mock.call(judge_venv=False), mock.call(judge_venv=True)])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main([]), 2)


class VersionsTest(unittest.TestCase):

    def test_the_ranges_hold_the_chosen_versions(self) -> None:
        """`versions.yaml` で Core を選んだら、 範囲を書き、 選んだ版を含める。"""
        self.assertIsNotNone(yaml, "PyYAML がありません（製品のパッケージの依存）")
        data = yaml.safe_load((ROOT / "versions.yaml").read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 2)
        for key, dist in (("image_acquisition_pdm", IA), ("pdm", PDM)):
            tag = data[key]["tag"]
            if tag is None:
                continue
            with self.subTest(core=key):
                rng = cores.RANGES[dist]
                self.assertIsNotNone(rng, f"{dist} を選んだのに範囲が未選択です")
                self.assertTrue(isinstance(tag, str) and tag.startswith("v"), tag)
                self.assertTrue(cores.in_range(tag[1:], *rng), f"{dist} {tag} が範囲の外です")


if __name__ == "__main__":
    unittest.main()

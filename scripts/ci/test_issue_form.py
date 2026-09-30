#!/usr/bin/env python3
"""Issue Form の種別（`type:`）と、 それを読む `issue-form.sh` を確かめる。

**ネットワークを使わない。** `gh` を差し替え、 手元の Form を正本の代わりに
返す。

なぜ見るのか。 直近の Issue 71 件はすべて種別なしだった。 Form も `/issue`
も種別を指定していなかったためで、 仕組みを足しても**値を入れる経路**が
無ければ使われない。 種別を Form の `type:` 1 箇所に書き、 CLI の
`/issue` もそこから読む。 どちらかが崩れると、 また種別なしの Issue に戻る。
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMS = ROOT / ".github" / "ISSUE_TEMPLATE"
SCRIPT = ROOT / ".claude" / "scripts" / "issue-form.sh"

#: `gh api repos/<正本>/contents/<Form> --jq .content` の代わり。
#: 正本の API と同じく base64 で返す。 読む場所は FAKE_FORMS。
FAKE_GH = """#!/usr/bin/env bash
for a in "$@"; do
  case "$a" in */ISSUE_TEMPLATE/*.yml) f="${a##*/}" ;; esac
done
base64 < "$FAKE_FORMS/$f"
"""


def _forms():
    """種別を持つべき Form（`config.yml` は Form ではない）。"""
    return sorted(p for p in FORMS.glob("*.yml") if p.name != "config.yml")


def _form_type(path: Path) -> "str | None":
    m = re.search(r"^type:\s*\"?([^\"\n]+?)\"?\s*$",
                  path.read_text(encoding="utf-8"), re.M)
    return m.group(1) if m else None


def _run(args, forms_dir=FORMS):
    """`issue-form.sh` を差し替えた gh で実行する。"""
    with tempfile.TemporaryDirectory() as d:
        gh = Path(d) / "gh"
        gh.write_text(FAKE_GH, encoding="utf-8")
        gh.chmod(0o755)
        env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}",
                   FAKE_FORMS=str(forms_dir))
        return subprocess.run(["bash", str(SCRIPT), *args], env=env,
                              capture_output=True, text=True)


def _filled_body(kind: str) -> str:
    """skeleton から、 必須項目と選択肢を埋めた本文を作る。"""
    r = _run(["skeleton", kind])
    first = {}
    for ln in r.stderr.splitlines():
        m = re.match(r"^  (.+?): (.+)$", ln)
        if m:
            first.setdefault(m.group(1), m.group(2))
    out = []
    for ln in r.stdout.splitlines():
        if ln.startswith("### "):
            label = ln[4:]
            out += [ln, "", first.get(label, "x"), ""]
    return "\n".join(out) + "\n"


class EveryFormHasATypeTest(unittest.TestCase):

    def test_every_form_names_its_issue_type(self) -> None:
        """**Form を足したときに `type:` を忘れると、 その Issue は種別なし。**"""
        self.assertTrue(_forms(), "Form が 1 件も見つかりません")
        for f in _forms():
            with self.subTest(form=f.name):
                self.assertTrue(_form_type(f), f"{f.name} に type: が無い")


class TheScriptReadsTheTypeTest(unittest.TestCase):
    """**種別を書き写さない。** Form の `type:` から読むこと。"""

    def test_the_skeleton_shows_the_type(self) -> None:
        for f in _forms():
            kind, want = f.stem, _form_type(f)
            with self.subTest(form=f.name):
                r = _run(["skeleton", kind])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"Issue の種別: {want}（gh issue create --type"
                              f" {want}）", r.stderr)
                self.assertNotIn("Issue の種別", r.stdout,
                                 "種別を本文（標準出力）に混ぜています")

    def test_the_check_names_the_type_right_before_creation(self) -> None:
        """check は作成の直前に実行する。 そこで `--type` を思い出させる。"""
        for f in _forms():
            kind, want = f.stem, _form_type(f)
            with self.subTest(form=f.name), tempfile.NamedTemporaryFile(
                    "w", suffix=".md", encoding="utf-8", delete=False) as b:
                b.write(_filled_body(kind))
            try:
                r = _run(["check", kind, b.name])
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("OK:", r.stdout)
                self.assertIn(f"--type {want}", r.stdout)
            finally:
                os.unlink(b.name)

    def test_a_form_without_a_type_says_so(self) -> None:
        """**種別が無いのに空の `--type` を案内しないこと。**"""
        with tempfile.TemporaryDirectory() as d:
            src = (FORMS / "feature.yml").read_text(encoding="utf-8")
            (Path(d) / "feature.yml").write_text(
                re.sub(r"^type:.*\n", "", src, flags=re.M), encoding="utf-8")
            r = _run(["skeleton", "feature"], forms_dir=Path(d))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Issue の種別: なし", r.stderr)
        self.assertNotIn("--type （", r.stderr)


if __name__ == "__main__":
    unittest.main()

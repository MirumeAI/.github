#!/usr/bin/env python3
"""顧客の値の混入検査が働くことを確かめる。

**このリポジトリは Public。** それなのに混入検査が無かった。 Private な
Core 2 件にはあり、 **必要な順番が逆になっていた**。

Private 側では実際に 3 回混入した。 どれも説明文・テストの見本・コメントに
顧客名を書いたもので、 **書いた本人は混入だと思っていなかった**。

見本は通す。 `192.0.2.x` `198.51.100.x` `203.0.113.x` は文書用に予約された
アドレスで、 繋ぎに行っても届かない。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import customer_data_check as C                            # noqa: E402

#: 見つかるべき値。 **本物ではない。**
FOUND = {
    "現場の IP（192.168）": "host = 192.168.1.20\n",
    "現場の IP（10.x）": "plc = 10.0.0.5\n",
    "顧客の名前": "# " + "mie" + "seiki の設定\n",
}

#: 通すべき値。
PASSED = {
    "文書用の予約 IP": "host = 192.0.2.20\n",
    "別の予約 IP": "host = 198.51.100.7\n",
    "版の番号": '{"v": "1.2.3"}\n',
    "ふつうの文": "この Repository は Public です。\n",
}


class ItFindsSiteValuesTest(unittest.TestCase):

    def _hits(self, body: str, name: str = "zz_probe.md") -> list:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / name
            p.write_text(body, encoding="utf-8")
            orig, C.ROOT = C.ROOT, Path(td)
            allow, C._ALLOW_CACHE = C._ALLOW_CACHE, {}
            try:
                return C.check(p)
            finally:
                C.ROOT, C._ALLOW_CACHE = orig, allow

    def test_site_values_are_found(self) -> None:
        for label, body in FOUND.items():
            with self.subTest(label=label):
                self.assertTrue(self._hits(body), f"{label} を見逃しています")

    def test_samples_pass(self) -> None:
        """**見本を落とさないこと。** 落とすと見本が書けなくなる。"""
        for label, body in PASSED.items():
            with self.subTest(label=label):
                self.assertEqual(self._hits(body), [],
                                 f"{label} を誤検出しています")


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
        got = C._allow(HERE.parents[1])
        self.assertTrue(got, "許可リストが空です")
        for path, why in got.items():
            with self.subTest(path=path):
                self.assertNotIn("理由が書かれていません", why)


class ItRunsOnThisRepositoryTest(unittest.TestCase):

    def test_the_current_contents_pass(self) -> None:
        """**Public に置いてよい状態であること。**"""
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
        own = (HERE.parents[1] / ".github" / "workflows"
               / "basic-checks.yml").read_text(encoding="utf-8")
        self.assertIn("customer_data_check.py", own,
                      "本 Repository の workflow が呼んでいません")
        reusable = (HERE.parents[1] / ".github" / "workflows"
                    / "basic-checks-reusable.yml").read_text(encoding="utf-8")
        self.assertNotIn("customer_data_check.py", reusable,
                         "再利用 workflow に入れると顧客 Repository で"
                         "毎回 NG になります")


if __name__ == "__main__":
    unittest.main()

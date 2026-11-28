# tests/ — この案件の試験

| 置くもの | 流し方 |
|---|---|
| `product/` | 製品のパッケージの、Core を使わない試験。この Repository の検査（`.github/workflows/basic-checks.yml` の `product` の job）が流す。手元では `python3 -m unittest discover -s tests/product`（PyYAML が要る） |
| Core を使う試験 | `versions.yaml` で選んだ Core の版の写しに対して流す。置くのは、その道具（`MirumeAI/.github#145`）ができてから |

`product/` には、どの案件にも当てはまる守り（Core の内部を import しない・`pyproject.toml` に Core の名前を書かない・`doctor` が動く）があります。外さずに足していきます。

## 置かないもの

- Core の機能そのものの試験（Core 側に置く）
- 顧客データ（画像を含む）、画像・Dataset・モデルと Model Weight の本体

試験のために作った小さなダミー（作り物の小さな画像・最小の設定など。顧客の物と、実際に撮った物とそれから作った物〔切り出し・縮小・加工〕を含まない）は置いてよい（正本は development-docs の `05_security/secrets_and_data.md` の「GitHub に置いてよいもの」）。

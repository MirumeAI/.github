# evaluation/ — 評価の結果と、値を選んだ記録

同じデータで測った結果と、この案件で値を選んだ記録（仮の置き場）を置きます。測ったデータの版（manifest の sha256・ファイルの数・大きさ）を必ず書きます。

## 置かないもの

- 毎回変わるデータでの結果（比べられない）
- 画像・Dataset・モデルの本体、データの manifest そのもの、データの置き場の実際の道（データの置き場に置く。ここには ID・ファイルの数・大きさ・manifest の sha256 だけを書く）
- ほかの案件の値・秘密・資格情報・顧客データ（混入の検査は照らさないので、Pull Request で Reviewer B が見る）

正本: `MirumeAI/development-docs` の `03_customer_development/data_and_models.md` と `customer_specific_development.md` の「顧客 Repository での開発」

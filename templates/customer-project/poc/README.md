# poc/ — 実現性の検証

技術的に成立するかを確かめた記録を置きます。

- 測ったデータの版（manifest の sha256・ファイルの数・大きさ）。評価に使うデータは後から変えない
- 試した方式と結果、主要なリスクが解消されたかどうか

## 置かないもの

- **本番のコード。** 共通機能は Core の Issue から正式化します
- 画像・Dataset・モデルの本体と、データの manifest そのもの

正本: `MirumeAI/development-docs` の `03_customer_development/data_and_models.md`

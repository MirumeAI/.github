# requirements/ — 顧客要求と検査仕様

**何を検査し、何を良品とするか**を決めた記録を置きます。

- 顧客からの要求（元の言葉のまま）
- 検査仕様（対象・面・不良の種類・判定の基準）
- 受入条件（判定できる形で書く）

## 置かないもの

- 顧客から提供された画像・図面・Dataset の本体（データの置き場に置き、ID と manifest の sha256 で指す）
- 実装の詳細（Core の責務。ここには「何を」だけを書く）

正本: `MirumeAI/development-docs` の `03_customer_development/data_and_models.md`

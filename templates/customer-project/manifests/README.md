# manifests/ — 移る間の物（今は置く物がありません）

このフォルダは、目標の並び（`MirumeAI/development-docs` の `03_customer_development/customer_repository_structure.md`）にありません。前は Dataset と Model の参照と hash を置く場所でした。今の決まり（同じ所の `data_and_models.md`）では、Git には「どのデータか」（ID・ファイルの数・大きさ・manifest の sha256）だけを、次の所に書きます。**ここに置く物はありません。** このフォルダの行き先は `MirumeAI/development-docs#422` で決めます。

| 書くこと | 書く所 |
|---|---|
| 採用したモデルの版・ファイルの数・大きさ・モデルの manifest の sha256 | System Release の記録 `release/<ID>.yaml` の `inspection` |
| 学習用と評価用の Dataset の ID・ファイルの数・大きさ・manifest の sha256 | 同じ記録の `dataset` |
| 値を選んだときに測ったデータの版（manifest の sha256） | 値を選んだ記録（決まるまでの仮の置き場は `evaluation/`） |

manifest の作り方と、後で中身が同じかを確かめる方法は、`data_and_models.md` の「manifest と hash」にあります。

## 置かないもの

- 画像・Dataset・Model Weight の**本体**（データの置き場に置く）
- **manifest そのもの**（ファイルごとの sha256 と道の一覧）。ファイルの名前に案件の値が入りうるので、データの置き場の側（dir の外）に置きます
- データの置き場の実際の道と、学習・評価に使った撮像回の一覧など中身の一覧（ID と manifest の sha256 で指す）

# machines/ — 機体の設定（目標の形）

目標の形では、機体ごとの値（`<機体の記号>.yaml`）を置きます。組み立て器と一緒に決めるまで、この雛形では始めません。

## 置くもの

- 説明の `.md` だけ

## 置かないもの

- 機体の設定のファイル（`recipes/case.yaml` が無いのにここに置くと、混入の検査が止まります）
- 開発の機体の設定（どの形でも Git の外に置く）
- 顧客データ（画像を含む）・Dataset・モデルと Model Weight の本体、Credential、パスワード、ライセンスファイル、ほかの案件の値

目標の形の正本: `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md`

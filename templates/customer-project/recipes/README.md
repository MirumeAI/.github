# recipes/ — レシピ（目標の形）

目標の形では、案件の値の正本です（`case.yaml`・`products/<製品の名前>.yaml`）。組み立て器と一緒に決めるまで、この雛形では始めません。

## 置くもの

- 説明の `.md`。今の案件の値は `config/`（配る）と `dev/`（開発の機体）に置きます

## 置かないもの

- `case.yaml` と `products/` のファイル（`case.yaml` を置くと、混入の検査と記録の検査が新しい形として照らします。`case.yaml` が無いのに `products/` にファイルがあると、混入の検査が止まります）
- 秘密・資格情報・顧客データ、ほかの案件の値
- Core のコードと、複数案件で使う共通の判定ロジック（Core へ還元する）

目標の形の正本: `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md`

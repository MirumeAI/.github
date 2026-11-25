# recipes/ — 案件の値（レシピ）

| 置くもの | 中身 |
|---|---|
| `case.yaml` | 案件に 1 つの値（案件の名前・面・カメラの役・判定の種類・使う機能） |
| `products/<製品の名前>.yaml` | 製品ごとの値（撮像の条件・切り出し・判定の設定）。ファイルの名前は製品の名前 |

- `case.yaml` があるので、混入の検査と記録の検査は、この Repository を新しい形として照らします（`recipes/`・`machines/` も照らす）
- `case.name` は Core が案件を引く鍵で、変えません。宣言（`customer-profile.yaml`）の `project.profiles` に載せます。製品の名前は `products` に載せます

## 置かないもの

- 機体の値（カメラの個体・通信の相手・置き場の道。`machines/` に置く）と、組み立て器が組む鍵
- 見本帳づくりが計算した値（モデルの側に置く）
- ほかの案件の値・秘密・資格情報・顧客データ
- Core のコードと、複数案件で使う共通の判定ロジック（Core へ還元する）

形の正本: `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md`

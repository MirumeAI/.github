# recipes/ — 製品・検査条件

製品ごとの検査条件を置きます。

## 前の形（今）

撮像の条件の記録は `recipes/capture/` に置きます。カメラごとの物理の条件の表（`README.md`）と、製品ごとの条件の写し（`<製品>.json`）です。形・決め方・照らし方の正本は `MirumeAI/Image-Acquisition-PDM` の `docs/capture_conditions.md` です。

前の形では、顧客の間の混入の検査は `recipes/` を照らしません。ほかの案件の値・IP・製造番号を置きません。秘密・資格情報・顧客データは、どの案件のものも置きません（Private でも同じ）。記録の Pull Request は人が中身を見て確かめます。

## 新しい形（移るまで作らない）

新しい形では、`recipes/` が案件の値の正本です（根の `README.md` の「目標の形と移る間」）。

```
recipes/case.yaml                  案件に 1 つの値（案件の記号と名前・面・カメラの役・判定の種類・使う機能）
recipes/products/<製品の名前>.yaml  製品ごとの値（撮像の条件・切り出し・判定の設定）
```

- 形の正本は `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md` です
- `recipes/case.yaml` を置くと、Repository は新しい形になり、顧客の間の混入の検査が `recipes/` と `machines/` を照らします。記録の検査も新しい形の照らしをします。組み立て器が入るまで置きません
- `recipes/case.yaml` が無いのに `recipes/products/` にファイルを置くと、混入の検査が止まります

## 置かないもの

- Core のコード
- 複数案件で使う共通の判定ロジック（Core へ還元する）
- 見本帳づくりが計算した値（モデルの側に置く）

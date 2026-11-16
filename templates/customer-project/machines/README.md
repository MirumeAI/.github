# machines/ — 機体の設定（新しい形）

新しい形（根に `recipes/case.yaml` がある）で、機体ごとの値を置く場所です。**この雛形は前の形なので、移るまで、ここに置く物はありません**（根の `README.md` の「目標の形と移る間」）。

```
machines/<機体の記号>.yaml   据え付けた機体（kind: installed）と顧客に見せる機体（kind: shown）の値
machines/dev.example.yaml    開発の機体の設定の見本（値を入れない。本物は Git の外に置く）
```

- 機体の記号は、`deploy/README.md` の対応表と同じにします
- 書ける鍵・書かない鍵・見本の形の正本は `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md` です
- `recipes/case.yaml` が無いのに、ここに説明の `.md` と空の `.gitkeep` のほかのファイルを置くと、顧客の間の混入の検査が止まります
- 開発の機体の設定（`kind: dev`）は Git に置きません。Git にあれば、混入の検査が止めます

## 置かないもの

- 開発の機体の本物の設定（Git の外に置く）
- 画像・Dataset・Model Weight の本体、Credential、パスワード、ライセンスファイル
- ほかの案件の値

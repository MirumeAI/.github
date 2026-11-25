# machines/ — 機体の値（機体の設定）

| 置くもの | 中身 |
|---|---|
| `<機体の記号>.yaml` | 据え付けた機体（`kind: installed`）と顧客に見せる機体（`kind: shown`）の値。記号は `deploy/README.md` の表と同じ |
| `dev.example.yaml` | 開発の機体の設定の見本（値を入れない） |

`profile` に書けるのは、機体だけの鍵と機体で上書きできる鍵です。IP と製造番号は、宣言（`customer-profile.yaml`）に載せた値だけを書きます。

## 置かないもの

- 開発の機体の本物の設定（`kind: dev`）。Git の外（案件のフォルダの `config/<機体の記号>.yaml`）に置きます。Git にあれば混入の検査が止めます
- 画像・Dataset・Model Weight の本体、Credential、パスワード、ライセンスファイル
- ほかの案件の値

形の正本: `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md`

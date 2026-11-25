# release/ — System Release の記録

現場へ投入できる組合せを、1 ファイル 1 版で記録します。

```
release/<System Release ID>.yaml     例: PJxxx-R2026.10.1.yaml
```

- Core の版は `versions.yaml` を参照します（写さない）。Model・設定・Dataset・実行環境・ハードウェアを合わせたものが System Release です
- 製品のパッケージ（`product_package`）とずれの記録は、目標の形（`recipes/case.yaml` がある）で載せます（正本の「新しい形の System Release」）

## 置かないもの

- データの manifest そのものと、データの置き場の実際の道（版か ID・ファイルの数・大きさ・manifest の sha256 で書く）
- `versions.dev.yaml`・`dev/`・`MIRUME_DEV_CONFIG` を指す書き方

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

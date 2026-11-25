# config/ — 機体へ配る設定

Core の据え付け手順（撮像側の `setup_customer.sh`）が機体へ配る設定を置きます。

```
config/image-acquisition/   撮像・判定側（カメラ、PLC、面、検査範囲、除外領域）
config/pdm/                 学習・評価側
```

検査の性質（`inspection` か `flow`）と `plc`・`nas`・`ai` の `enabled` は必ず書きます。設定を変えたら、同じ Pull Request で `customer-profile.yaml` も直します。

## 置かないもの

- 開発の機体だけの設定（`dev/` に置く）
- Core の Source、Credential、ライセンスファイル、パスワード

照らし方の正本: `MirumeAI/development-docs` の `03_customer_development/customer_isolation_check.md`

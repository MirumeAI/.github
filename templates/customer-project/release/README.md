# release/ — System Release の manifest

現場へ投入できる組合せを1ファイル1版で記録します。

```
release/<System Release ID>.yaml     例: PJxxx-R2026.10.1.yaml
```

**Core の版だけでは、どの Model とどの閾値で動くかが決まりません。** `versions.yaml` は Core を commit SHA で固定しますが、それに Model・設定・データセット・実行環境・ハードウェアを合わせたものが System Release です。

`versions.yaml` の内容は**写さず参照**します。写すと二重管理になり、片方を直しても効きません。

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

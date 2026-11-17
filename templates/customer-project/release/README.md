# release/ — System Release の manifest

現場へ投入できる組合せを1ファイル1版で記録します。

```
release/<System Release ID>.yaml     例: PJxxx-R2026.10.1.yaml
```

**Core の版だけでは、どの Model とどの閾値で動くかが決まりません。** `versions.yaml` は Core を commit SHA で固定しますが、それに Model・設定・データセット・実行環境・ハードウェアを合わせたものが System Release です。

`versions.yaml` の内容は**写さず参照**します。写すと二重管理になり、片方を直しても効きません。

採用したモデル（`inspection` の `version`・`model_sha256`・`model_files`・`model_bytes`）と、学習用・評価用の Dataset（`dataset`）は、版か ID・ファイルの数・大きさ・manifest の sha256 で書きます。データの manifest そのものと、データの置き場の実際の道は書きません（`MirumeAI/development-docs` の `03_customer_development/data_and_models.md` の「Git に置くもの」）。

新しい形（根に `recipes/case.yaml` がある）では、製品のパッケージも載せます。組み立て器が使うレシピと機体の設定の commit は、`inspection.config_commit`（40 桁）です。

```yaml
product_package:
  name: <製品のパッケージの配布の名前>
  version: "<版>"                      # 引用符で囲む
  wheel_sha256: "<wheel の 64 桁の sha256>"
```

**ずれを残したまま、次の System Release を作りません。** System Release を足す Pull Request の前に、製品のパッケージのある System Release を配って今 `ACTIVE` の機体のすべてで、足す System Release の `inspection.config_commit` と比べたずれの確かめ（`deployment/` の README）を足し、ずれ無し（`products: {}`）にします。ずれの確かめの記録は、System Release を足す Pull Request より前に、配置の記録の Pull Request で入れます（System Release を足す Pull Request に足せる配置の記録は、その System Release を配った記録だけです）。記録の検査が、その Pull Request で足した System Release と、製品のパッケージか `inspection.config_commit` を書き換えた System Release について照らします。配置に使った System Release の、この 2 つは変えません（新しい System Release を作ります）。

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

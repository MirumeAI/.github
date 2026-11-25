# dev/ — 開発の機体だけの設定

開発の機体で、この案件を動かすための設定を置きます。**機体へは配りません**（`setup_customer.sh` は `config/` だけを配る）。

```
dev/config/image-acquisition/<案件の名前>.json   撮像側の開発の設定（MIRUME_DEV_CONFIG に、このファイルの絶対の道を渡す）
```

- ファイルの名前（拡張子を除く）は案件の名前にします（Core が案件を引く鍵）
- 混入の検査が `config/` と同じに照らします。`dev/config/` に置けるのは上のファイルと説明の `.md` だけです
- `MIRUME_DEV_CONFIG` は開発の機体だけで使います（据え付けた機体の起動は `INSPECTION_CONFIG`）

## 置かないもの

- 出荷・据え付けに使う設定（`config/` に置く）
- `deploy/`・`versions.yaml`・`release/`・`deployment/` から `dev/` と `MIRUME_DEV_CONFIG` を指す書き方（検査が断る）
- 画像・Dataset・Model Weight の本体、Credential、パスワード、ライセンスファイル、ほかの案件の値

正本: `MirumeAI/development-docs` の `03_customer_development/customer_isolation_check.md`（S4・S6）

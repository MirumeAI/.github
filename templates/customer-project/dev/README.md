# dev/ — 開発の機体だけの設定

開発の機体（開発サーバ・各自の開発の PC）で、この案件を動かすための設定を置きます。**機体へは配りません。** Core 側の据え付け手順（`setup_customer.sh`）は `config/` だけを配るので、ここは据え付けた機体にも顧客に見せる機体にも届きません。

```
dev/config/image-acquisition/<案件の名前>.json   撮像側の開発の設定（MIRUME_DEV_CONFIG に、このファイルの絶対の道を渡す）
```

## 置くもの

- 撮像側の開発の設定。保存済み画像のカメラ（`"sdk": "file"`）など、開発の機体だけで動かす形にします。**ファイルの名前（拡張子を除く）は、この案件の名前にします。** 撮像側と PDM は、固定した設定のファイルの名前を案件の名前として使い、機体の値（`configs/machine.json` の `work_roots`）や見本帳を引く鍵にします。ほかの案件の名前と重ねません
- 説明の `.md`

## 置かないもの

- 出荷・据え付けに使う設定（`config/` に置く）
- 画像・Dataset・Model Weight の本体、Credential、パスワード、ライセンスファイル
- ほかの案件の値

## 守ること

- `MIRUME_DEV_CONFIG` は開発の機体だけで使います。顧客に見せる機体を含め、開発の機体でないものでは使いません。据え付けた機体の起動は `INSPECTION_CONFIG` を必ず渡します（両方あるときは `INSPECTION_CONFIG` が先です）
- `deploy/`・`versions.yaml`・System Release と配置の記録（`release/`・`deployment/`）から、`dev/` と `MIRUME_DEV_CONFIG` を指しません（顧客の間の混入の検査と、記録の検査が断ります。`deploy/` の説明の `.md` は照らさないので、そこにも書きません）。製品の起動の script が `MIRUME_DEV_CONFIG` を環境から外す（`env -u`・`env` に渡す `-u` の並び・`unset` など）のは守りなので、かまいません
- 案件の値が入るので、顧客の間の混入の検査が `config/` と同じに照らします。`dev/config/` の下に置けるのは `image-acquisition/<案件の名前>.json` と説明の `.md` だけです。宣言（`customer-profile.yaml`）で使うものを開発の機体で切る（`enabled: false`）のはかまいません（`plc`・`nas`・`ai` の節を書いたら、`enabled` を `true` か `false` で書きます。無いと検査が断ります）。宣言に無いカメラ・面・宛先を足すときは、宣言も同じ Pull Request で直します
- `dev/config/` の外の `dev/` は、混入の検査が照らしません。範囲が広がるまでは、`dev/` を変える Pull Request で Reviewer B がほかの案件の値・秘密・資格情報が入っていないかを見ます。置くものを増やすときは、先に置き場の形と検査の範囲を決めます

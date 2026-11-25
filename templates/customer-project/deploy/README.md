# deploy/ — 起動・配置の設定

この機体だけに要る起動・配置のファイル（systemd unit・デスクトップの起動・自動起動の登録と解除）と、機体の記号の対応表を置きます。

## 置かないもの

- Core の script の写し（Core の `scripts/` から使う。unit の `WorkingDirectory` も Core に向ける）
- `dev/` と `MIRUME_DEV_CONFIG` を指す書き方（混入の検査が、説明の `.md` のほかのファイルで断る。説明の `.md` は照らさないが、そこにも書かない）
- `versions.dev.yaml` を指す書き方（開発の版を機体に配らない）

## 機体の記号

配置と検証の記録には、機体を中立の記号（例: `M01`。英大文字で始まる英大文字と数字の 2〜8 字）で書きます。IP・製造番号・装置名は書きません。記号と実物の対応は、この表に置きます（棚卸しのときに、配置の持ち主が書く）。

| 記号 | この dir の中の、その機体の設定 | 置いた所（現場・ラインの中立の言い方） |
|---|---|---|
| M01 | `<dir の名前>/` | <現場・ライン> |

正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

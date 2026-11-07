# deploy/ — この機体固有の配置設定

この機体だけに必要な起動・配置のためのファイルを置きます。

- systemd unit（この機体のパスを焼き込んだもの）
- デスクトップ起動の設定、アイコン
- 自動起動の登録・解除スクリプト

## 置かないもの

**Core の script を複製しないでください。**

機体上には Core があり、そこに同じものがあります。複製すると drift する場所を増やすだけです。実際に、ある案件で `check_pdm_link.sh` などが複製されており、片方が古くなっていました。

Core の script は Core の `scripts/` から使います。systemd unit も `WorkingDirectory` を Core に向け、`scripts/...` を相対で呼びます。

今ある機体（LEGACY）では、Core の作業コピーがこの Repository の根の `PDM/`・`Image-Acquisition-PDM/` にあります（`.gitignore` 済み）。新しい配置はこの形に頼りません（`MirumeAI/development-docs` の `03_customer_development/core_verification_status.md` の H5）。開発では Core の clone をこの Repository の隣に置きます（根の `README.md` の「Core の置き方」）。

## 機体の記号

配置の記録・検証の記録には、機体を中立の記号（例: `M01`。英大文字で始まる英大文字と数字の 2〜8 字）で書きます。IP・製造番号・装置名は書きません。記号と実物の対応は、この README の表に置きます（棚卸しのときに、配置の持ち主が書く）。

| 記号 | この dir の中の、その機体の設定 | 置いた所（現場・ラインの中立の言い方） |
|---|---|---|
| M01 | `<dir の名前>/` | <現場・ライン> |

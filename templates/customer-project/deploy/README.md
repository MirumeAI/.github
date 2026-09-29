# deploy/ — この機体固有の配置設定

この機体だけに必要な起動・配置のためのファイルを置きます。

- systemd unit（この機体のパスを焼き込んだもの）
- デスクトップ起動の設定、アイコン
- 自動起動の登録・解除スクリプト

## 置かないもの

**Core の script を複製しないでください。**

機体上には Core の作業コピーがあり、そこに同じものがあります。複製すると drift する場所を増やすだけです。実際に、ある案件で `check_pdm_link.sh` などが複製されており、片方が古くなっていました。

Core の script は Core 作業コピーの `scripts/` から使います。systemd unit も `WorkingDirectory` を Core 作業コピーに向け、`scripts/...` を相対で呼びます。

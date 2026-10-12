# MirumeAI GitHub Default Templates

MirumeAI Organization 配下の Repository で共通利用する Issue / Pull Request テンプレートです。

> **重要**
> この Repository (`MirumeAI/.github`) は Public で運用します。
> 顧客名、社内URL、IPアドレス、秘密情報、具体的な認証情報などは記載しないでください。

## Issue Forms

- `Feature` : 新機能・機能改善・ドキュメント改善
- `Bug` : 不具合修正
- `Experiment` : AI・アルゴリズム・評価実験

## 基本フロー

1. Issue を作成する
2. Issue の目的と完了条件をチーム内で共有する
3. Issue 番号を含む Branch を作成する
4. 実装・検証する
5. Pull Request を作成する
6. 原則 Squash and merge する（レビュー可能なメンバーがいる場合は Review 後）
7. Issue を Close する

### Branch 命名例

- `feature/42-camera-reconnect`
- `fix/51-login-error`
- `experiment/73-patchcore-backbone`

## 適用範囲

この `.github` Repository のテンプレートは、MirumeAI Organization 配下の各 Repository に同種の独自テンプレートがない場合に既定値として利用されます。

## 顧客の間の混入の検査（`customer-profile.yaml`）

顧客 Repository の設定に、別の案件の値・ファイル・構成が混ざっていないかを、その案件自身の宣言（Repository の根の `customer-profile.yaml`）と照らします（`scripts/ci/customer_isolation_check.py`）。ほかの顧客の値も、顧客の名前の登録表も、secret も使いません。

有効にするのは、宣言を置いた顧客 Repository だけです（既定は `false`。指定しない Repository は何も変わりません）。

```yaml
jobs:
  checks:
    uses: MirumeAI/.github/.github/workflows/basic-checks-reusable.yml@main
    with:
      run-isolation-check: true
```

手元で流すには PyYAML が要ります。

```bash
python -m pip install pyyaml
python scripts/ci/customer_isolation_check.py --root <顧客 Repository> --report-only
```

| 終了コード | 意味 |
|---|---|
| 0 | 合格（`--report-only` のときは、混入の疑いがあっても 0） |
| 1 | 混入の疑い（宣言の外の値・ファイル・構成） |
| 2 | 宣言・設定・検査の誤り（宣言が無い・読めない・資格情報らしい鍵がある、PyYAML が無い など）。照らせないときは合格にしません |

見るもの:

- 層 1: カメラの IP と製造番号、PLC の宛先と番地、トリガを決まった場所から取り出し、宣言と照らす。補いとして、決まった場所の外の IPv4 と `trig_` で始まる語も照らす
- S1: `config/` の下は、Core が読む決まった名前か、宣言した追加のファイルだけ（説明の `.md` と空の `.gitkeep` は除く）。`config/pdm/` は、配る道具が同じ並びのまま配る `.json`・`.txt`（`defaults/` は除く）
- S2: PLC・NAS・AI を使うかが宣言と同じか。PLC を使わないなら、`plc` は `{"enabled": false}` だけ
- S3: 構成の名前（`_profile`）・flow・カメラ・面が宣言の中か
- S4: `deploy/` と `versions.yaml` が触れる設定のファイルが、実在して許されているか。`deploy/` の中の値は参考として出すだけ

宣言の例（値は文書用の架空のもの）:

<!-- customer-profile.yaml の例 -->
```yaml
schema_version: 1
project:
  flow: metal                    # config.json の flow と同じ
  profiles: [example-site]       # config.json の _profile に使ってよい名前
capabilities:                    # 使うかどうか（推測しないので、必ず書く）
  plc: true
  nas: false
  ai: true
equipment:
  cameras:
    - {id: camera1, ip: 192.0.2.11, serial: EXAMPLE-SN-0001}
    - {id: camera2, ip: 192.0.2.12, serial: EXAMPLE-SN-0002}
endpoints:                       # カメラと PLC のほかの宛先。ちょうどの値で書く
  - 192.0.2.50
  - network: 192.0.2.64/28       # 網は本当に要るときだけ。理由を書き、/24 より広い網は書けない
    reason: 架空の例。機器の IP が DHCP で変わる
plc:
  endpoints: ["192.0.2.5:5000"]
  addresses: [EXAMPLE_D100, EXAMPLE_D101]
triggers: [trig_example_top, trig_example_side]
faces: [trig_example_top, trig_example_side]
config_files:
  extra:                         # Core が読む決まった名前の外に置くファイル。理由を書く
    - path: config/image-acquisition/example_extra.json
      reason: 架空の例
extensions: []
```

- 宣言には、その案件の自分の値だけを書きます。資格情報（パスワード・トークン・鍵）は書きません（書くと終了コード 2）
- `127.` と `255.` で始まる IP、`0.0.0.0`、Core の鍵 `trig_bit`・`trig_ready` は宣言しなくて構いません。文書用の網（`192.0.2.x` など）は除きません
- 値は出力しません。出すのは種類・ファイル・行・欄だけです（決まっていない名前は `<*>` と `<伏せ#n>`）
- PR では、基準の commit の宣言と比べた増減の数を出します。増えていれば `PROFILE EXPANSION DETECTED` と出るので、増えた値が本当にこの案件のものかをレビューで確かめてください（設定と宣言の両方へ同じ誤った値を足すと、照合では見つかりません）

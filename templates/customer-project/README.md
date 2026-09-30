# <案件の名前>

この Repository は **この案件だけの差分**を持ちます。Core の Source は持ちません。

```
PDM Core + Image-Acquisition-PDM Core + この Repository = 顧客検査システム
```

## 使う Core の版

`versions.yaml` が Core を **commit SHA で固定**します。浮動 Branch 名や `latest` で本番構成を管理しません。どの版で動いているかが分からないと、不具合の再現も切り戻しもできません。

## 構造

| 場所 | 何を置くか |
|---|---|
| `requirements/` | 顧客要求と検査仕様、受入条件 |
| `quick-diagnosis/` | 案件を受けるかの初期確認と判断 |
| `poc/` | 実現性の検証（**本番のコードは置かない**） |
| `config/` | Core へ配る設定 |
| `manifests/` | Dataset と Model の**参照と hash**（本体は置かない） |
| `evaluation/` | 固定した評価の結果 |
| `validation/` | 仕様に対する技術検証 |
| `acceptance/` | 顧客・現場が受け入れた記録 |
| `release/` | System Release の manifest |
| `deployment/` | 配置した事実の記録 |
| `deploy/` | この機体固有の起動・配置設定 |
| `plugins/` | この案件固有の拡張（Interface は未検証） |
| `recipes/` | 製品・検査条件 |
| `tests/` | この案件の組合せの検証 |

各フォルダの README に「置くもの / 置かないもの」が書いてあります。

## 置かないもの

- Core の Source（`PDM` と `Image-Acquisition-PDM` の中身）
- 顧客から提供された画像・Dataset・Model Weight の**本体**
- ライセンスファイル（`*.lic`）、Credential、パスワード

`config/` には PLC の接続先など内部ネットワークの情報が入ります。**この Repository は Private にし、Public な場所へ内容を転記しないでください。**

## この雛形から新しい案件を作る

**Repository の作成と設定は人が行います。** Agent は Repository の設定を変更できません（`MirumeAI/development-docs` の DECISION D5）。

1. Repository を作る（Private）

   ```bash
   gh repo create MirumeAI/customer-<会社>-<部品> --private
   ```

2. この雛形を中身として入れる

   ```bash
   gh api repos/MirumeAI/.github/tarball/main | tar -xz --strip-components=1 \
     -C <作業ディレクトリ> '*/templates/customer-project'
   ```

3. Custom Properties を 8 つすべて設定する（Organization の管理者が行う）

   | Property | 値 |
   |---|---|
   | `repo_type` / `lifecycle` / `owner_team` | `customer-project` / `active` / `delivery` |
   | `domain` / `criticality` | 案件に合わせる（検査なら `visual-inspection` / `high`） |
   | `data_classification` | `customer-confidential` |
   | `production_impact` / `governance_profile` | 稼働前は `none` / `standard`。現場で稼働したら `direct` / `strict` |

   設定後に `MirumeAI/.github` の `scripts/ci/org_audit.py`（読み取りだけ）を実行し、所見に出ないことを確かめる。
   値の意味は `MirumeAI/development-docs` の `00_management/repository_management.md`
4. Team の権限を確認する（`developers=write` / `maintainers=admin`）
5. `versions.yaml` の commit SHA を埋める
6. `CLAUDE.md` を置く（正本は `MirumeAI/development-docs`）
7. `config/` に案件の設定を入れ、Core 側の `setup_customer.sh` で機体へ配る

## 参照

- 開発ルールの正本: `MirumeAI/development-docs`
- ReleaseとDeploymentの記録の形: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

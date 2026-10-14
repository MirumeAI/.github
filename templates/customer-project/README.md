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

## 始めるときにやること

この Repository は `MirumeAI/.github` の `scripts/ci/new_customer_repo.py` で作りました。Team の権限、Merge の設定、Dependabot Alerts、分類（Custom Properties）は、作ったときに設定済みです。作り方は `MirumeAI/development-docs` の `03_customer_development/customer_repository_structure.md` にあります。

1. `versions.yaml` に、使う Core の commit SHA を書く
2. `config/` に案件の設定を入れ、Core 側の `setup_customer.sh` で機体へ配る。同じ Pull Request で `customer-profile.yaml` も書く（下の「顧客の間の混入の検査を始める」）
3. 現場で稼働したら、分類の `production_impact` と `governance_profile` を `direct` と `strict` に変える（Organization の管理者が行う）。稼働前は `none` と `standard`

### 顧客の間の混入の検査を始める

根の `customer-profile.yaml` は、この案件にあってよい識別子の宣言です。作ったときは**値の無い初期形**で、状態は人の確認待ちです。初期形のままでは検査が照らせずに止まり、合格になりません。そのため、混入の照合（`.github/workflows/basic-checks.yml` の `run-isolation-check`）は `false` で始まります。運用の決まりの正本は `MirumeAI/development-docs` の `03_customer_development/customer_isolation_check.md` です。

1. `config/` に案件の設定を書く（`config/image-acquisition/config.json` が無いと、検査は照らせない）
2. 同じ Pull Request で `customer-profile.yaml` の下書きを書く。書き方は `MirumeAI/.github` の README「顧客の間の混入の検査」。状態の行は確認待ちのまま。今の設定にあることは、この案件の値である根拠にならない。値を Issue・Pull Request の本文・CI の記録に書かない
3. 報告だけの形で流す（PyYAML が要る）。終了コードではなく、最後の行と `NG` の行を見る。`NG` は分けて直し、確かめずに宣言へ値を足して黙らせない

   ```bash
   python <MirumeAI/.github の clone>/scripts/ci/customer_isolation_check.py --root . --report-only
   ```

4. 人が宣言の値を案件の資料と 1 つずつ照らして確かめ、自分で Pull Request に記録する（記録の形は上の正本の「人の確認」。Agent は確かめた扱いにしない）
5. 状態の行を `状態: VERIFIED（確かめた Pull Request の番号）` に直す（値は変えない）
6. `--report-only` を付けずに流して `結果: PASS` になったら、`run-isolation-check` を `true` にする
7. Pull Request の CI で、混入の照合の段が動いて `結果: PASS` になることを確かめ、Merge の門に従って merge する

初めて値を書く Pull Request では、基準（初期形）が読めないので、CI の宣言の増減は「比べていません」と出て、`PROFILE EXPANSION DETECTED` は出ません。宣言を変えた Pull Request は、知らせが出なくても、人が確かめるまで merge しません。

## 参照

- 開発ルールの正本: `MirumeAI/development-docs`
- ReleaseとDeploymentの記録の形: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

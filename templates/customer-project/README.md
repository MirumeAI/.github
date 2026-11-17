# <案件の題名>

この Repository は **この案件だけの差分**を持ちます。Core の Source は持ちません。

```
PDM Core + Image-Acquisition-PDM Core + この Repository = 顧客検査システム
```

## 使う Core の版

`versions.yaml` が出荷に使う Core を **commit SHA で固定**します。浮動 Branch 名や `latest` で本番構成を管理しません。どの版で動いているかが分からないと、不具合の再現も切り戻しもできません。

開発中に Core の直しを試すときは、根の `versions.dev.yaml`（任意）に Core ごとの 40 桁の commit を書けます。**開発の版は機体に配置しません**（System Release が参照するのは `versions.yaml` だけです）。

## Core の置き方

- **開発**: Core の clone は、この Repository の下でなく隣に置きます（同じ親のフォルダに `PDM/`・`Image-Acquisition-PDM/`・この Repository を並べる）。下に置くと、この Repository のコードが Core の内部を誤って参照しえます
- **今ある機体（LEGACY）**: この案件に今ある機体がある場合だけ、その機体の上では、この Repository の根の `PDM/`・`Image-Acquisition-PDM/` に Core の作業コピーがある形で動いています（`.gitignore` 済み。今ある機体が無い案件でも、下に clone したときの守りとして残します）。新しい配置はこの形に頼りません
- **新しい形**（下の「目標の形と移る間」）: 製品のパッケージが両 Core を wheel の依存として読み込み、clone の道に頼りません。入れ方の正本は `MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md` の「製品のパッケージの入れ方」です

## 構造

| 場所 | 何を置くか |
|---|---|
| `requirements/` | 顧客要求と検査仕様、受入条件 |
| `quick-diagnosis/` | 案件を受けるかの初期確認と判断 |
| `poc/` | 実現性の検証（**本番のコードは置かない**） |
| `config/` | Core へ配る設定 |
| `manifests/` | Dataset と Model の**参照と hash**（本体は置かない） |
| `evaluation/` | 固定した評価の結果と、この案件で値を選んだ記録 |
| `validation/` | 仕様に対する技術検証 |
| `acceptance/` | 顧客・現場が受け入れた記録 |
| `release/` | System Release の manifest |
| `deployment/` | 配置した事実の記録 |
| `deploy/` | この機体固有の起動・配置設定 |
| `dev/` | 開発の機体だけの設定（機体へ配らない。`dev/config/image-acquisition/<案件の名前>.json` を `MIRUME_DEV_CONFIG` に渡す） |
| `app/` | 製品アプリ（起動・製品画面・API・Adapter。Core の公開の面ができた範囲から） |
| `recipes/` | 製品・検査条件（新しい形では、案件の値の正本） |
| `machines/` | 機体の設定（新しい形。移るまで置く物は無い） |
| `tests/` | この案件の組合せの検証 |
| `.claude/`・`scripts/ci/` | Claude Code 一式（hook・command）と手元で流す検査。**正本（`MirumeAI/.github`）の写し**で、作るときに道具が写した。この Repository では直さない（正本との照合で、ずれると検査が止まる。直すときは正本を直して同期する）。`.claude/settings.json` も直さない（正本に `*.lic` の拒否を足して作った物で、照合の対象の外なので、ずれても検査では止まらない。変えるときは正本の `MirumeAI/.github` の Pull Request から） |
| `.github/workflows/` | Pull Request の検査。正本の再利用 workflow を呼ぶだけ（検査の中身は正本にある） |

各フォルダの README に「置くもの / 置かないもの」が書いてあります。

## 目標の形と移る間

この雛形は**前の形**です。案件の値は `config/` に置き、Core 側の `setup_customer.sh` で機体へ配ります。

目標の形は、この Repository を、案件を組み立てる 1 つの製品のパッケージにする形です。両 Core を wheel の依存として読み込み、起動の命令 1 つで組み立てます。案件の値の正本は `recipes/`、機体の設定は `machines/` です（正本は `MirumeAI/development-docs` の `03_customer_development/customer_repository_structure.md`。`MirumeAI/development-docs#392`）。

新しい形への移り方は、最初の案件で作って確かめてから、この雛形に入れます（#392 の段 4 と段 6）。それまでは、新しい形の置き場を作りません。

- 作らない物: `recipes/case.yaml`・`recipes/products/`・`machines/` の中のファイル（説明の `.md` を除く）・根の `pyproject.toml`・`src/`
- 理由: `recipes/case.yaml` があると、混入の検査と記録の検査が新しい形として照らします。組み立て器が無いので、起動もできません。`case.yaml` が無いのに `machines/`・`recipes/products/` にファイルがあると、混入の検査が止まります

| 前の形（今） | 新しい形の行き先 |
|---|---|
| `config/` | `recipes/case.yaml` と `machines/<機体の記号>.yaml`（鍵ごとに分ける）。面ごとに判定する流れの設定（`config/pdm/` など）と門番の設定（`pdm_guard_policy.json`）は、版 1 では行き先が無く、今の置き場のまま（`MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md` の「まだ決めないこと」） |
| `dev/` | Git の外の開発の機体の設定（Git には見本の `machines/dev.example.yaml` だけ） |
| `app/` | `src/<パッケージ>/`（製品のパッケージ） |
| `recipes/capture/` | `recipes/products/<製品の名前>.yaml` |
| 差し込む部品 | `src/<パッケージ>/` の部品を、根の `pyproject.toml` の entry points で Core の差し込みの群に登録する（群ができてから） |

この Repository では、設定と記録に加えて、この案件の開発もします（値を選んだ記録・回帰の試験・製品アプリ。製品アプリは Core の公開の面ができた範囲から、公開の面だけを使います）。公開の面に無いものが要るときは Core の内部を使わず、この Repository に改善依頼を書きます。共通プラットフォームの側が分類し、Core に足すものは Core の Issue にします（`platform_evolution.md` の 4 章）。見つかった汎用の機能は Core へ還元します（`MirumeAI/development-docs` の `03_customer_development/customer_specific_development.md` の「顧客 Repository での開発」「Coreへの還元」と `platform_evolution.md`）。

## 置かないもの

- Core の Source（`PDM` と `Image-Acquisition-PDM` の中身）
- 顧客から提供された画像・Dataset・Model Weight の**本体**
- ライセンスファイル（`*.lic`）、Credential、パスワード

`config/` には PLC の接続先など内部ネットワークの情報が入ります。**この Repository は Private にし、Public な場所へ内容を転記しないでください。**

## 始めるときにやること

この Repository は `MirumeAI/.github` の `scripts/ci/new_customer_repo.py` で作りました。Team の権限、Merge の設定、Dependabot Alerts、分類（Custom Properties）は、作ったときに設定済みです。作り方は `MirumeAI/development-docs` の `03_customer_development/customer_repository_structure.md` にあります。

1. `versions.yaml` は未選択（`tag` と `commit` が `null`）で始まる。使う Core を決めたら、Product Release のタグと、タグが指す commit を書く（`pdm` の行は撮像側のタグの `pdm.lock.json` と同じにする）
2. `config/` に案件の設定を入れ、Core 側の `setup_customer.sh` で機体へ配る。同じ Pull Request で `customer-profile.yaml` も書く（下の「顧客の間の混入の検査を始める」）
3. 現場で稼働したら、分類の `production_impact` と `governance_profile` を `direct` と `strict` に変える（Organization の管理者が行う）。稼働前は `none` と `standard`

### 顧客の間の混入の検査を始める

根の `customer-profile.yaml` は、この案件にあってよい識別子の宣言です。作ったときは**値の無い初期形**で、状態は人の確認待ちです。初期形のままでは検査が照らせずに止まり、合格になりません。そのため、混入の照合（`.github/workflows/basic-checks.yml` の `run-isolation-check`）は `false` で始まります。運用の決まりの正本は `MirumeAI/development-docs` の `03_customer_development/customer_isolation_check.md` です。

1. `config/` に案件の設定を書く（`config/image-acquisition/config.json` が無いと、検査は照らせない）。据え付けの計画が無く、開発の機体だけの設定 `dev/config/image-acquisition/<案件の名前>.json` だけを置く案件は、`config/` に説明のほかを置かなければ、検査がその設定を主の設定の代わりに照らす（`dev/README.md`）
2. 同じ Pull Request で `customer-profile.yaml` の下書きを書く。書き方は `MirumeAI/.github` の README「顧客の間の混入の検査」。状態の行は確認待ちのまま。今の設定にあることは、この案件の値である根拠にならない。値を Issue・Pull Request の本文・検査の記録に書かない
3. 報告だけの形で流す（PyYAML が要る。`MirumeAI/.github` の clone は main の最新にしてから流す）。終了コードではなく、最後の行と `NG` の行を見る。`NG` は分けて直し、確かめずに宣言へ値を足して黙らせない。`参考` の行の数（値なし）はこの Repository の Issue に記録し、`注意` の行は人が読む

   ```bash
   python <MirumeAI/.github の clone>/scripts/ci/customer_isolation_check.py --root . --report-only
   ```

4. 作成者と別の人が、宣言の値を案件の資料と 1 つずつ照らして確かめ、自分で Pull Request に記録する（記録の形は上の正本の「人の確認」。Agent は確かめた扱いにしない）
5. 状態の行を `状態: VERIFIED（確かめた Pull Request の番号）` に直す（値は変えない。YAML として読んだ値が確かめた版と同じことを Pull Request に書く）
6. `--report-only` を付けずに流して `結果: PASS`（`NG` と `ERROR` の行が無い）になったら、`run-isolation-check` を `true` にする
7. Pull Request の Required Verification（`MirumeAI/.github` の `scripts/ci/run_required_verification.py` を手元で流す）で、混入の照合の段が動いて `結果: PASS` になることを確かめ、Merge の門に従って merge する

初めて値を書く Pull Request では、基準（初期形）が今の決まりでは読めないので、検査の宣言の増減は空の宣言と比べて、すべてを増えたものとして数え、`PROFILE EXPANSION DETECTED` を出します。宣言を変えた Pull Request は、知らせが出ても出なくても、人が確かめるまで merge しません。

## 参照

- 開発ルールの正本: `MirumeAI/development-docs`
- ReleaseとDeploymentの記録の形: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

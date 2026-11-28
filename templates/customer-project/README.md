# <案件の題名>

<案件の題名> の案件だけの差分（設定・記録・製品のパッケージ）を持つ Repository です。Private にし、内容を Public な場所へ写しません。

## 目的

この案件のシステムを、全案件で共通の Core と組み合わせて作ります。Core は一般の機能だけを持ち、この案件だけの値・記録・製品のパッケージはここに置きます。案件ごとの違いを Core に入れないことで、どの案件も同じ Core を使い、Core の直しが全案件に届くようにしています。決まりの正本は `MirumeAI/development-docs` の `03_customer_development/` です。

## 仕組み

組み合わせる Core は事業ごとに変わります。この Repository は外観検査システムの形です。

```
PDM Core + Image-Acquisition-PDM Core + この Repository = 顧客検査システム
```

| 部分 | 役目 |
|---|---|
| PDM | Platform・Web の画面・システム管理・製品と検査の管理 |
| Image-Acquisition-PDM | カメラの撮像・検査の実行・判定処理・機器との連携 |
| この Repository | 案件の設定と記録、Core の公開の面と差し込みの群だけを使う製品のパッケージ |

使う Core の版は `versions.yaml`（Product Release のタグと commit）で選びます。製品のパッケージは Core の wheel と組み、Core の名前を `pyproject.toml` に書きません。

### Core の置き方

開発で Core の clone を使うときは、この Repository の下でなく隣に置きます（根の `PDM/`・`Image-Acquisition-PDM/` は `.gitignore` 済み。目標の形では、Core を clone するのは管理者チームだけ）。製品のパッケージの入れ方の正本は `recipes_and_machines.md` の「製品のパッケージの入れ方」です。

## 構成

| 場所 | 何のためか |
|---|---|
| `customer-profile.yaml` | 顧客の間の混入の検査の宣言 |
| `versions.yaml` | 出荷に使う Core の組合せ（開発中の固定は任意の `versions.dev.yaml`。機体に配らない） |
| `pyproject.toml`・`src/case_package/` | 製品のパッケージ（命令 `doctor` と、組める Core の版の範囲 `cores.py`） |
| `tests/product/` | Core を使わない試験 |
| `config/` | 機体へ配る案件の設定（外観検査では撮像側の `setup_customer.sh` で配る） |
| `dev/` | 開発の機体だけの設定（機体へ配らない） |
| `recipes/`・`machines/` | 目標の形のレシピと機体の設定の置き場（組み立て器と一緒に決めるまで、説明のほかは置かない） |
| `deploy/` | 起動・配置の設定と、機体の記号の対応表 |
| `evaluation/`・`requirements/`・`acceptance/` | 評価と値を選んだ記録、顧客要求と検査仕様・受入の記録 |
| `release/`・`deployment/`・`validation/` | System Release・配置・検証の記録 |
| `.claude/`・`scripts/ci/`・`.github/workflows/` | 正本（`MirumeAI/.github`）の写しと、共通の検査を呼ぶ workflow。ここでは直さない |

Core の Source とその写し、顧客の画像・Dataset・Model Weight の本体、データの manifest そのものとデータの置き場の実際の道、ライセンスファイル（`*.lic`）・資格情報（パスワードを含む）は置きません（正本は development-docs の `05_security/secrets_and_data.md`。各フォルダの README に、置く物と置かない物があります）。

## 始め方

1. 使う Core を決めたら、`versions.yaml` に Product Release のタグと commit を書き、同じ Pull Request で `src/case_package/cores.py` の範囲を書く
2. 製品のパッケージを、Web と判定の venv の依存を変えない形で入れ（正本は `recipes_and_machines.md` の「製品のパッケージの入れ方」）、`<案件の名前> doctor` で確かめる
3. 下の「顧客の間の混入の検査を始める」

### 顧客の間の混入の検査を始める

`customer-profile.yaml` は値の無い初期形で始まり、混入の照合（`.github/workflows/basic-checks.yml` の `run-isolation-check`）は `false` です。設定と一緒に宣言を書き、作成者と別の人が値を案件の資料と照らして確かめてから有効にします（Agent は確かめた扱いにしない。宣言を変えた Pull Request は、人が値を確かめるまで merge しない）。値を Issue・Pull Request の本文・検査の記録に書かず、所見（`NG`）を消すために確かめずに宣言へ値を足しません。順の正本は development-docs の `customer_isolation_check.md` の「有効にする順」、宣言の書き方は `MirumeAI/.github` の `scripts/ci/README.md` の「顧客の間の混入の検査」です。

## 詳しい文書

- この Repository は `MirumeAI/.github` の `scripts/ci/new_customer_repo.py` で作りました（作り方は `customer_repository_structure.md`）
- 決まりの正本: development-docs の `03_customer_development/`（並びは `customer_repository_structure.md`、レシピと機体の設定は `recipes_and_machines.md`、混入の検査は `customer_isolation_check.md`。この README の書式は `02_github_workflow/github_rules.md` の「README の書式」）

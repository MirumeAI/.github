# <案件の題名>

この Repository は **この案件だけの差分**（設定・記録・製品のパッケージ）を持ちます。Core の Source は持ちません。

```
PDM Core + Image-Acquisition-PDM Core + この Repository = 顧客検査システム
```

決まりの正本は `MirumeAI/development-docs` の `03_customer_development/` です（並びは `customer_repository_structure.md`、レシピと機体の設定は `recipes_and_machines.md`）。

## 構造

| 場所 | 置くもの |
|---|---|
| `customer-profile.yaml` | 混入の検査の宣言（下の「顧客の間の混入の検査を始める」） |
| `versions.yaml` | 出荷に使う Core の組合せ（Product Release のタグと commit）。開発中の固定は任意の `versions.dev.yaml`（機体に配らない） |
| `pyproject.toml`・`src/case_package/` | 製品のパッケージ（命令 `doctor` と、組める Core の版の範囲の表 `cores.py`） |
| `tests/product/` | Core を使わない試験（検査の `product` の job が流す） |
| `config/` | 案件の値で、機体へ配る設定（撮像側の `setup_customer.sh` で配る） |
| `dev/` | 開発の機体だけの設定（機体へ配らない） |
| `recipes/`・`machines/` | 目標の形のレシピと機体の設定の置き場。組み立て器と一緒に決めるまで、説明のほかは置かない |
| `deploy/` | 起動・配置の設定と、機体の記号の対応表 |
| `evaluation/` | 評価の結果と、値を選んだ記録 |
| `release/`・`deployment/`・`validation/` | System Release・配置・検証の記録 |
| `requirements/`・`acceptance/` | 顧客要求と検査仕様・受入の記録 |
| `.claude/`・`scripts/ci/`・`.github/workflows/` | 正本（`MirumeAI/.github`）の写しと、その再利用 workflow を呼ぶ検査。ここでは直さない |

各フォルダの README に「置くもの・置かないもの」があります。

## Core の置き方

- 開発では、Core の clone をこの Repository の下でなく隣に置きます（根の `PDM/`・`Image-Acquisition-PDM/` は `.gitignore` 済み）
- 製品のパッケージは Core の wheel と組みます。Core の名前は `pyproject.toml` に書かず、組める版の範囲を `src/case_package/cores.py` に持ちます。入れ方の正本は `recipes_and_machines.md` の「製品のパッケージの入れ方」です

## 置かないもの

- Core の Source とその写し
- 顧客の画像・Dataset・Model Weight の本体、データの manifest そのもの、データの置き場の実際の道
- ライセンスファイル（`*.lic`）・Credential・パスワード

この Repository は Private にし、内容を Public な場所へ写しません。

## 始めるときにやること

この Repository は `MirumeAI/.github` の `scripts/ci/new_customer_repo.py` で作りました（作り方は `customer_repository_structure.md`）。

1. 使う Core を決めたら、`versions.yaml` に Product Release のタグと commit を書き、同じ Pull Request で `src/case_package/cores.py` の範囲を書く
2. 製品のパッケージを Web と判定の venv に依存を入れない形で入れ、`<案件の名前> doctor`（判定の venv は `--judge-venv`）で確かめる
3. 下の「顧客の間の混入の検査を始める」

### 顧客の間の混入の検査を始める

`customer-profile.yaml` は、この案件にあってよい識別子の宣言です。作ったときは値の無い初期形で、検査は照らせずに止まるので、混入の照合（`.github/workflows/basic-checks.yml` の `run-isolation-check`）は `false` で始まります。

1. `config/` に案件の設定を書き、同じ Pull Request で `customer-profile.yaml` の下書きを書く（状態の行は確認待ちのまま。値を Issue・Pull Request の本文・検査の記録に書かない）。主の設定 `config/image-acquisition/config.json` が無いと照らせません（開発の機体の設定だけを置く案件の扱いは、正本の「照らす範囲と前提」）
2. 報告だけの形で流す: `python <MirumeAI/.github の clone>/scripts/ci/customer_isolation_check.py --root . --report-only`。終了コードは 0 なので、最後の行と `NG` の行を見る。`NG` は分けて直し、確かめずに宣言へ値を足して消さない
3. 作成者と別の人が、宣言の値を案件の資料と照らして確かめ、自分で Pull Request に記録する（Agent は確かめた扱いにしない）
4. 状態の行を `状態: VERIFIED（確かめた Pull Request の番号）` に直す。値は変えず、YAML として読んだ値が確かめた版と同じことを Pull Request に書く
5. `--report-only` を付けずに流して `結果: PASS`（`NG` と `ERROR` の行が無い）になったら、`run-isolation-check` を `true` にする
6. その Pull Request の Required Verification で、混入の照合の段が動いて `結果: PASS` になることを確かめる

宣言を変えた Pull Request は、人が値を確かめるまで merge しません。正本は `customer_isolation_check.md` の「有効にする順」と、`MirumeAI/.github` の README「顧客の間の混入の検査」です。

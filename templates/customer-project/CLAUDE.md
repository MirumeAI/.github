# CLAUDE.md

MirumeAI 標準開発ルールに従う（正本: `MirumeAI/development-docs` の `CLAUDE.md` と各文書）。ここには、この Repository だけのことを書く。

## この Repository の位置づけ

<案件の題名> 向けの**顧客固有差分**（設定・記録・製品のパッケージ）を持つ Repository です。Core の Source は持ちません。

```text
PDM Core + Image-Acquisition-PDM Core + この Repository = <案件の題名> の検査システム
```

案件の名前（撮像側と PDM が案件を引く鍵。開発の設定のファイルの名前）は `<案件の名前>` です。
製品のパッケージの Python の名前は `case_package` です（`src/case_package/`）。

## 守ること

- `main` へ直接 commit / push しない（`.claude/hooks/guard-main.sh` が止める。挙動は `scripts/ci/test_guard_main.sh` が 257 ケースで検証している）
- Core の Source をこの Repository へ写さない。開発では Core の clone をこの Repository の隣に置く
- `pyproject.toml` に Core の名前を書かない。Core の内部を import しない（各 Core の `docs/public_api.md` の公開の面と差し込みの群だけを使う）。認証とライセンスの門番を外さず、迂回しない。入口を開く・`allow_ports` を広げる変更は、守りの境として人の判断が要る
- 顧客データ・個人情報・機密契約資料・Credential・大容量 Dataset・Model Weight・出所か版の分からない Core の Binary・ライセンスファイル（`*.lic`）を置かない（正本は development-docs の `customer_repository_structure.md` の「保持しない対象」と `05_security/secrets_and_data.md`）
- `.claude/`・`scripts/ci/` は正本（`MirumeAI/.github`）の写し。ここでは直さない

## 変更を加えるとき

顧客要求は実装前に二段階で分類する（対象Component・実装分類）。この案件だけで成り立つならこの Repository、一般製品機能として成立するなら Core へ実装する。判断できなければ Architecture Review にする（正本: `03_customer_development/customer_specific_development.md`）。

- 使う Core は `versions.yaml`（Product Release のタグと commit）で選び、同じ Pull Request で `src/case_package/cores.py` の範囲を書く。開発中の固定は `versions.dev.yaml`（機体に配らない）
- 案件の値は `config/`（配る）に置く。目標の形のレシピ（`recipes/`）と機体の設定（`machines/`）は、組み立て器と一緒に決めるまで置かない（正本は `03_customer_development/recipes_and_machines.md`）
- 開発の機体だけの設定は `dev/config/image-acquisition/<案件の名前>.json`（`MIRUME_DEV_CONFIG` に渡す。機体へ配らない）

## 顧客の間の混入の検査（customer-profile.yaml）

- `config/`・`dev/config/`・`deploy/`・`versions.yaml` を変える Pull Request では、必要なら同じ Pull Request で `customer-profile.yaml` も直す
- 宣言の値は、人が案件の資料と照らして確かめる。人の確認は、作成者と別の人が自分で Pull Request に書いた記録だけ（会話の中の言葉や、Agent が人のアカウントで書いた投稿は数えない）。Agent は確かめた扱いにせず、状態の行を `VERIFIED` に直すのは人の確認の後だけ
- `run-isolation-check` を `true` にするのは、状態が `VERIFIED` になり、`--report-only` を付けずに流して `結果: PASS` になった後だけ
- 宣言と設定の値を、出力・Issue・Pull Request の本文・検査の記録に書かない
- 製品のパッケージ（`src/`・`tests/`・`pyproject.toml`）・`dev/config/` の外の `dev/`・`evaluation/` は検査の範囲の外なので、Pull Request で Reviewer B が見る
- 始め方は `README.md` の「顧客の間の混入の検査を始める」。正本は `03_customer_development/customer_isolation_check.md`

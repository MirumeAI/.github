# CLAUDE.md

MirumeAI 標準開発ルールに従う。
ルールの正本: `MirumeAI/development-docs` の `CLAUDE.md` および各ドキュメント。

## 絶対に守ること

1. `main` へ直接 commit / push しない。force push しない。
2. 原則 Issue から始める。Branch 名に Issue 番号を含める。
3. 変更は Pull Request 経由で `main` に入れる。Merge は Squash and merge。
4. **Core の Source をこの Repository へコピーしない。** 開発では Core の clone を
   この Repository の隣に置く（下の「Core の置き方」）。根の `PDM/` と
   `Image-Acquisition-PDM/` は、今ある機体（LEGACY）の作業コピーの置き場で、`.gitignore` 済み。
5. 顧客データ、Credential、大容量 Dataset、Model Weight を置かない。
6. ライセンスファイル（`*.lic`）を置かない。

1 は `.claude/hooks/guard-main.sh` が機械的に拒否する。`cd` と `git -C` の対象
Repository を解決してから Branch を判定し、 先頭の飾り（`env`、`bash -c`、
絶対パス等）も剥がすため、 書き方を変えた程度では抜けない。
`gh` の書き込み系サブコマンドと、 Claude の Edit / Write によるガードと設定の書き換えも拒否する（ガードの変更は `MirumeAI/.github` の Pull Request で案を出し、人の承認と merge の後に正本から同期する）。

**ただしこれは「うっかり」を止める壁であって、 敵対的な回避を完全に塞ぐものではない。**
ターミナルから直接実行した場合は防げない。 GitHub Free かつ Private Repository では
Branch protection を設定できないため、 これと運用ルールが唯一の防護である。
挙動は `scripts/ci/test_guard_main.sh` が 304 ケースで検証している。

## この Repository の位置づけ

<案件の名前> 向けの**顧客固有差分**（設定と記録と、この案件の開発）を持つ Repository です。Core の Source は持ちません。

```text
PDM Core + Image-Acquisition-PDM Core + この Repository = <案件の名前> の検査システム
```

出荷に使う Core の組合せは `versions.yaml` で固定します。

## Core の置き方

- **開発**: Core の clone は、この Repository の下でなく隣に置く（同じ親のフォルダに
  `PDM/`・`Image-Acquisition-PDM/`・この Repository を並べる）。下に置くと、この Repository の
  コードが Core の内部を誤って参照しうる。使う commit は `versions.yaml`（出荷の組合せ）、
  開発中に Core の直しを試すときは根の `versions.dev.yaml`（任意。Core ごとの 40 桁の commit
  だけ）。**開発の版は機体に配置しない**（System Release が参照するのは `versions.yaml` だけ）
- **今ある機体（LEGACY）**: 機体の上では、この Repository の根の `PDM/`・`Image-Acquisition-PDM/`
  に Core の作業コピーがある形で動いている（`.gitignore` 済み）。新しい配置はこの形に頼らない
  （`MirumeAI/development-docs` の `03_customer_development/core_verification_status.md` の H5）
- 正本は `MirumeAI/development-docs` の `03_customer_development/platform_evolution.md`（2 章）と
  `customer_specific_development.md` の「開発中の固定」

## 変更を加えるとき

顧客要求は実装前に二段階で分類する。

- 対象Component: `PDM` / `Image-Acquisition-PDM` / `Both` / `Customer only`
- 実装分類: `Config` / `Core` / `Core + Config` / `Plugin・Adapter` / `Architecture Review`

**この案件だけで成り立つならこの Repository、一般製品機能として成立するなら Core** へ実装する。
顧客名を除いても成立する機能をこの Repository に閉じ込めない。判断できない場合は
Architecture Review としてチームに相談する。

この Repository で開発してよいもの（正本は `MirumeAI/development-docs` の `03_customer_development/customer_specific_development.md` の「顧客 Repository での開発」）:

- 今から: この案件で値を選んだ記録（形と置き場は `MirumeAI/development-docs#273` で決める。決まるまでの仮の置き場は `evaluation/`。混入の検査が照らさないので、記録の Pull Request では Reviewer B が混入を見る）
- 回帰の試験（`tests/`）: 最初の試験は、`versions.yaml` で選んだ Core の版に対して流す道具（`MirumeAI/.github#145`）ができてから
- 製品アプリ（`app/`。起動・製品画面・API・Adapter）: Core の公開の面（各 Core の `docs/public_api.md`）ができた範囲から。公開の面と入口だけを使い、Core の内部は import しない。認証とライセンスの門番を外さず、迂回しない（PDM 側の製品の API と画面は門番の内側に置く）。製品アプリのコードは混入の検査の範囲の外なので、Pull Request で Reviewer B が、ほかの案件の値・秘密・資格情報・Core の写し・Core の内部の使用・門番の迂回を見る
- 開発の機体だけの設定（`dev/config/image-acquisition/<案件の名前>.json`）: 今から。`MIRUME_DEV_CONFIG` に渡す。機体へ配らず、`deploy/`・`versions.yaml`・`release/`・`deployment/` から指さない。混入の検査が `config/` と同じに照らす（`dev/README.md`）。`dev/config/` の外の `dev/` は混入の検査の範囲の外なので、Pull Request で Reviewer B が混入を見る
- 差し込みの入口のコード（`plugins/`）: 差し込む Core の入口ができてから。入口は、具体の要求が出たときに Architecture Review で作る
- 公開の面に無いものが要るときは、Core の内部を使わずに、この Repository に改善依頼を書く（顧客の名前と値を書かない形で）。共通プラットフォームの側が分類し、Core の共通の不具合・機能の不足・入口の不足は Core の Issue にする（`MirumeAI/development-docs` の `03_customer_development/platform_evolution.md` の 4 章）

次のどれかに当たったら、Core の Issue を作って還元する（同じ文書の「Coreへの還元」）: 2 件目の案件でも要る・Core の内部を使わないと作れない・System Release を作るときの見直しで上げられると分かった。

Core を変更した場合は、Core 側で Release したうえで `versions.yaml` を
検証済みの組合せへ更新する（Product Release のタグは人が打つ）。

## 顧客の間の混入の検査（customer-profile.yaml）

- 設定（`config/`・`dev/config/`・`deploy/`・`versions.yaml`）を変える Pull Request では、必要なら根の
  `customer-profile.yaml` も同じ Pull Request で直す
- 宣言の値は、人が案件の資料と照らして確かめる。今の設定にあることは根拠にならない。
  Agent は確かめた扱いにせず、状態の行を `VERIFIED` に直すのは人の確認の後だけ。
  人の確認は、作成者と別の人が自分で Pull Request に書いた記録だけ（会話の中の言葉や、
  Agent が人のアカウントで書いた投稿は数えない）
- `run-isolation-check` を `true` にするのは、状態が `VERIFIED` になり、`--report-only` を
  付けずに流して `結果: PASS`（`NG` と `ERROR` の行が無い）になった後だけ
- 宣言と設定の値を、出力・Issue・Pull Request の本文・検査の記録に書かない
- 始め方は `README.md` の「顧客の間の混入の検査を始める」。運用の決まりの正本は
  `MirumeAI/development-docs` の `03_customer_development/customer_isolation_check.md`

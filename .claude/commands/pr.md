---
description: 共通 PR テンプレートに沿った Pull Request を、事前チェックのうえ最小限の分量で作成する
argument-hint: [関連Issue番号]
---

現在の Branch: !`git branch --show-current`
main からの差分: !`git diff main...HEAD --stat | tail -30`

## 1. 事前チェック

本文を書く前に確認し、問題があれば先に指摘する。

1. Issue の作業範囲から逸脱した変更が混ざっていないか
2. 秘密情報・認証情報・顧客データ・`.env`・大容量ファイルが含まれていないか
3. 不要な生成物やログが含まれていないか
4. Issue の完了条件 / 成功条件を満たしているか
5. 必要なテスト・動作確認・実験を実施したか
6. 本文と commit の文に、顧客名・顧客の値・社内 URL・IP・認証情報・手元の道を書いていないか（`MirumeAI/.github` は Public。本文は作った時点で公開される）

## 2. テンプレートを取得する

項目を自分で書かず、正本から取得する。

```bash
gh api repos/MirumeAI/.github/contents/.github/PULL_REQUEST_TEMPLATE.md --jq .content | base64 -d
```

取得した見出しと順序を変えない。項目を追加・削除しない。

## 3. 分量のルール

**必要なことだけを書く。**

| 項目 | 目安 |
|---|---|
| 変更目的 | 1〜2文 |
| 変更内容 | 箇条書き。1項目1行 |
| 確認結果 | 実施した確認を箇条書き。5項目以内 |
| 影響範囲 | 箇条書き3項目以内 |
| Reviewerに確認してほしい点 | 3点以内 |
| 本文全体 | 60行以内 |

書かないこと。

- 実装の経緯、試行錯誤、調査ログ。必要ならコメントへ書く
- 差分を読めば分かることの逐一の説明
- 他の項目の繰り返し

## 4. 記載のルール

- 関連Issue は `Closes #<番号>` を使う
- 実施していない確認項目のチェックボックスを埋めない。未実施なら未実施と書く
- 自動テストが無い Repository では、その旨と実際に行った確認を書く
- 該当しない項目は「対象外」と1行で書く。長い説明を付けない
- Experiment は成功条件に対する結果を記載する
- 顧客差分を含む場合は対象Componentと分類を記載する。`Both` の場合は、顧客 Repository の Issue（1 つ）と、各 Core の PR / Release と、顧客側で採用する Version 組合せを書く（Core 側で独立した Issue を作ったときは、それも書く）

## 5. 検査の記録

作成の前に、この Branch の head で Required Verification（正本の検査）を流し、PASS の記録の行を確認結果に書く。
先に、`.github` の clone で `git pull --ff-only`、この Repository の clone で `git fetch origin` をしておく（道具は自分で取り込まない。古いままだと、古い基準や古い正本で流れる）。

```bash
python3 <.github の clone>/scripts/ci/run_required_verification.py <この Repository の clone> --head "$(git rev-parse HEAD)"
```

- 流し方と記録の行の読み方は、`MirumeAI/.github` の README の「merge の前の検査」
- 終了コード 1（FAIL）・2（流せない）のときは、直してから流し直す（今ある長期 Branch〔LEGACY〕を base にした PR で流せない〔終了コード 2〕ときだけは、development-docs の `02_github_workflow/pull_request_review.md` の「Merge の門」の LEGACY の決まりに従い、木にある試験を流して PASS した記録を書く。FAIL は LEGACY でも直してから流し直す）。「基準を含む: いいえ」なら、main を取り込んでから流し直す。飛ばした試験が 0 でなければ、理由を確かめる
- head が変わったら流し直し、記録の行を書き直す

## 6. 作成

本文をユーザーに見せ、承認を得てから Draft で作成する（依頼が作成まで任せているときは、そのまま作成してよい）。

```bash
gh pr create --draft --repo MirumeAI/<repository> --base main --title "<要約>" --body-file <ファイル>
```

今ある長期 Branch（LEGACY）への直しの PR は `--base <長期 Branch>` にする。同じ直しを `main` へ戻す PR は、`main` から切った別の Branch で作る（長期 Branch を head にしない。merge のときに head の Branch が消える設定のため）。決まりは development-docs の `03_customer_development/customer_specific_development.md` の「禁止事項」。

**この command では merge しない。** merge は、development-docs の `02_github_workflow/pull_request_review.md` の「Merge の門」の条件をすべて満たしてから行う（head の固定・Required Verification の PASS・Reviewer A・B の BLOCKER・MAJOR・IN-PR REQUIRED FIXES が 0・人の判断が要る変更でない）。LEGACY を base にした PR は、道具が流せないときだけ Required Verification を木にある試験の PASS の記録で代え、いつも人の判断の後に merge する。人の判断が要る変更と、follow-up が守り・構成・本番・壊す操作の判断を含むときは、人の判断を待つ。人の確認（Human Verification）が要る PR（顧客 Repository の混入の検査の宣言の値を変える PR など）は、人が自分で書いた人の確認の記録も待つ。

このリポジトリの変更なら `/docs-sync` の確認も併せて実施する。

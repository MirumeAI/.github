---
description: development-docs / .github の変更時に必要な整合確認と changelog 追記を行う
---

GitHub 運用、`MirumeAI/.github`、`MirumeAI/development-docs` を変更したので、次を確認・実施してください。

## 1. 現状文書との整合

`00_management/current_github_operation.md` を読み、今回の変更後の実態と一致しているか確認する。
特に次を取り違えないこと。

- **実装済み**: リポジトリ内のファイルで確認できる
- **運用方針**: `development-docs` でルールとして定めている
- **GitHub 設定未確認**: 設定画面を見ないと強制状態を判断できない

確認していない GitHub 設定を「実装済み」と書かない。

## 2. changelog 追記

`00_management/implementation_changelog.md` の「変更履歴」先頭に1件追加する。

```markdown
### YYYY-MM-DD 変更の要約

- **関連Issue／Pull Request**: #番号／URL
- **変更内容**:
- **変更理由**:
- **影響範囲**:
- **社内開発手順書で更新すべき章**:
```

Issue / PR 番号が未発行なら `未作成` と記録し、発行後に更新する。

## 3. .github との矛盾確認

- Issue Form / PR Template の**実物**と説明文が食い違っていないか
- 食い違う場合は `MirumeAI/.github` の実物を優先し、両リポジトリを同じ変更で同期する
- `MirumeAI/.github` は Public。顧客名・顧客固有仕様・社内URL・IPアドレス・認証情報が入っていないか

## 4. 文字コード

`00_`〜`05_` 配下と `README.md` / `CONTRIBUTING.md` / `ENCODING.md` は UTF-8 with BOM を維持する。
`CLAUDE.md` と `.claude/` 配下は BOM なし。

確認コマンド:

```bash
for f in $(git diff --name-only main...HEAD | grep '\.md$'); do
  printf '%s: ' "$f"; head -c 3 "$f" | od -An -tx1
done
```

`ef bb bf` で始まっていれば BOM あり。

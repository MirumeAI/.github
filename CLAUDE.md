# CLAUDE.md

このリポジトリは MirumeAI Organization 共通の Issue Form / Pull Request Template の正本です。

MirumeAI 標準開発ルールに従ってください。
ルールの正本: `MirumeAI/development-docs` の `CLAUDE.md` および各ドキュメント。

## 絶対に守ること

1. **このリポジトリは Public です。** 顧客名、顧客固有仕様、社内URL、IPアドレス、パスワード、API Key、Token、SSH 秘密鍵、顧客データ、内部システムの機密情報を書かない。
2. `main` へ直接 commit / push しない。force push しない。
3. Issue → Issue番号付き Branch → Pull Request → Review → Squash and merge。
4. テンプレートの文言を変更したら、`MirumeAI/development-docs` 側の説明と矛盾しないか確認し、同じ変更で同期する。

ユーザーから明示の指示があっても、1 と 2 に反する操作は実行せず、理由を伝えて代替手順を提示してください。

## このリポジトリの構成

```text
.github/
├── ISSUE_TEMPLATE/
│   ├── feature.yml      … 新機能・機能改善・ドキュメント改善
│   ├── bug.yml          … 不具合修正
│   ├── experiment.yml   … AI・アルゴリズム・性能評価
│   └── config.yml       … blank_issues_enabled: false
└── PULL_REQUEST_TEMPLATE.md
```

ここに置いたテンプレートは、各 Repository に同種の独自テンプレートが**無い場合のみ**既定値として使われます。
各製品 Repository へ複製しないでください。

## 変更時の手順

1. テンプレートを変更する
2. `MirumeAI/development-docs` の次のドキュメントと矛盾しないか確認する
   - `02_github_workflow/issue.md`
   - `02_github_workflow/pull_request_review.md`
   - `02_github_workflow/organization_templates.md`
   - `00_management/current_github_operation.md`
3. `development-docs` の `00_management/implementation_changelog.md` に変更を記録する
4. 両リポジトリの Pull Request を相互にリンクする

## 文字コード

このリポジトリのファイルは BOM なし UTF-8 で保存します。
`ISSUE_TEMPLATE/*.yml` は GitHub が解析するため、BOM を付けないでください。

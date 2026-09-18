# MirumeAI Development Workflow

MirumeAI Organization 配下の開発では、原則として以下の流れで変更を行います。

1. Issueを作成する
2. Issueの目的・背景・完了条件を確認する
3. Issue番号を含むBranchを作成する
4. 実装・検証する
5. Pull Requestを作成する
6. Reviewを受ける
7. Mergeする
8. IssueをCloseする

## Branch naming

- Feature: `feature/<issue-number>-<short-description>`
- Bug: `fix/<issue-number>-<short-description>`
- Experiment: `experiment/<issue-number>-<short-description>`

例:

```text
feature/42-camera-reconnect
fix/51-login-error
experiment/73-patchcore-backbone
```

## Pull Request

Pull Requestには関連Issueを記載し、可能な場合は `Closes #<issue-number>` を使用してください。

詳細な社内開発手順は Private Repository の `MirumeAI/development-docs` を参照してください。

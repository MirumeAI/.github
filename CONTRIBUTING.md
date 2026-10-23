# MirumeAI Development Workflow

MirumeAI Organization 配下の開発では、原則として以下の流れで変更を行います。

1. Issueを作成する
2. Issueの目的・背景・完了条件を確認する
3. Issue番号を含むBranchを作成する
4. 実装・検証する
5. 手元の検査（Required Verification）を流し、PASS の記録の行を書いて Pull Requestを作成する（`MirumeAI/.github` の `scripts/ci/run_required_verification.py`。使い方は同じ Repository の README）
6. レビュー可能なメンバーがいる場合はReviewを受ける。独立したレビュー（Reviewer A・B）を受けたら、Merge の門（`MirumeAI/development-docs` の `02_github_workflow/pull_request_review.md`）の条件を満たしてから merge する。Agent が merge するのは、独立したレビューと Merge の門を通したときだけ（人の判断が要る変更は人の判断の後）。人の確認は、誰が merge しても省かない（同じ文書の「どの Pull Request でも省かないこと」）
7. merge する head で検査が PASS していることを確かめ、原則としてSquash and mergeする（head が変わったら流し直す）
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

---
description: Issue 番号付きの作業 Branch を MirumeAI 命名規則で作成する
argument-hint: <Issue番号> [短い説明]
allowed-tools: Bash(git switch:*), Bash(git pull:*), Bash(git status:*), Bash(git branch:*)
---

現在の状態:
- Branch: !`git branch --show-current`
- 変更: !`git status --short | head -20`

引数「$ARGUMENTS」から Issue 番号と短い説明を読み取り、作業 Branch を作成してください。

## 手順

1. Issue 番号が指定されていなければ、作成せずに番号を尋ねる。
2. Issue の種別に応じて prefix を決める。不明なら尋ねる。
   - Feature → `feature/` / Bug → `fix/` / Experiment → `experiment/`
3. 短い説明は英小文字とハイフンにする（例: `camera-reconnect`）。日本語や空白を含めない。
4. 未コミットの変更がある場合は、先にユーザーへ扱いを確認する。
5. 次を実行する。

```bash
git switch main
git pull origin main
git switch -c <prefix>/<Issue番号>-<短い説明>
```

例: `feature/42-camera-reconnect` / `fix/51-login-error` / `experiment/73-patchcore-backbone`

## 注意

- 1 Branch = 1 Issue。無関係な変更を混ぜない。混ざりそうなら Issue の分割を提案する。
- 顧客別の長期 Branch は作らない。
- Core Repository に顧客別 Branch を作らない。

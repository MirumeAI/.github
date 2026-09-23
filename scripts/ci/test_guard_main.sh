#!/usr/bin/env bash
# guard-main.sh の回帰テスト。
#
# **主張ではなく実行で確かめる。** 以前は「N ケース確認した」と PR 本文に
# 書くだけで、 次に直したとき同じ確認ができなかった。
#
#   bash scripts/ci/test_guard_main.sh
#
# 終了コード 0 で全件期待どおり、 1 で不一致あり。
set -uo pipefail

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.claude/hooks/guard-main.sh"
[ -f "$HOOK" ] || { echo "hook が見つかりません: $HOOK" >&2; exit 2; }

PASS=0; FAIL=0
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# 保護 Branch 上の Repository と、 作業 Branch 上の Repository を用意する
setup_repo() {
  local dir="$1" branch="$2"
  mkdir -p "$dir"; git -C "$dir" init -q -b main
  git -C "$dir" -c user.email=t@e -c user.name=t commit -q --allow-empty -m init
  [ "$branch" = "main" ] || git -C "$dir" switch -q -c "$branch"
}
setup_repo "$WORK/on_main" main
setup_repo "$WORK/on_feature" feature/1-x

run_hook() {
  local cmd="$1" cwd="$2" tool="${3:-Bash}"
  local payload
  payload="$(python3 -c '
import json,sys
t,c=sys.argv[1],sys.argv[2]
key="file_path" if t in ("Edit","Write") else "command"
print(json.dumps({"tool_name":t,"tool_input":{key:c}}))' "$tool" "$cmd")"
  ( cd "$cwd" && printf '%s' "$payload" | bash "$HOOK" >/dev/null 2>&1 )
  [ $? -eq 2 ] && echo DENY || echo ALLOW
}

t() {
  local exp="$1" cmd="$2" cwd="${3:-$WORK/on_feature}" tool="${4:-Bash}"
  local got; got="$(run_hook "$cmd" "$cwd" "$tool")"
  if [ "$got" = "$exp" ]; then PASS=$((PASS+1));
  else FAIL=$((FAIL+1)); printf '  NG  期待=%-5s 実際=%-5s  %s\n' "$exp" "$got" "$cmd"; fi
}

echo "== 素直な書き方 =="
t DENY  "git push origin main"
t DENY  "git push origin HEAD:main"
t DENY  "git push origin refs/heads/main"
t DENY  "git push --force origin feature/1-x"

echo "== 検証で実証された回避 =="
t DENY  "git push origin HEAD:refs/heads/main"
t DENY  "git push origin +main"
t DENY  "git push origin +HEAD:main"
t DENY  "git push origin main:main"
t DENY  "git push --force-with-lease origin main"
t DENY  "git push --force-if-includes origin main"
t DENY  "git push up main"
t DENY  "git -C $WORK/on_main commit -m x"
t DENY  "git -c user.name=x push origin main"
t DENY  "git push origin :main"
t DENY  "git push origin --delete main"
t DENY  "git push -d origin main"
t DENY  "git push --mirror origin"
t DENY  "git push origin  main"

echo "== 先頭の飾りによる回避 =="
t DENY  "env git push origin main"
t DENY  "command git push origin main"
t DENY  "/usr/bin/git push origin main"
t DENY  "(git push origin main)"
t DENY  "{ git push origin main; }"
t DENY  "bash -c 'git push origin main'"
t DENY  "sh -c \"git push origin main\""
t DENY  "eval 'git push origin main'"
t DENY  "\\git push origin main"
t DENY  "sudo git push origin main"

echo "== cwd を変える回避 =="
t DENY  "cd $WORK/on_main && git commit -m x"
t DENY  "cd $WORK/on_main && git push"
t DENY  "git -C $WORK/on_main push"

echo "== 保護Branch 上での操作 =="
t DENY  "git commit -m x"          "$WORK/on_main"
t DENY  "git commit -n -m x"       "$WORK/on_main"
t DENY  "git commit --amend"       "$WORK/on_main"
t DENY  "git merge feature/x"      "$WORK/on_main"
t DENY  "git push"                 "$WORK/on_main"
t DENY  "git add . && git commit -m x" "$WORK/on_main"

echo "== gh による回避 =="
t DENY  "gh api repos/o/r/issues -f title=x"
t DENY  "gh api repos/o/r/issues --method=POST -f title=x"
t DENY  "gh api -XPOST repos/o/r/issues"
t DENY  "gh api repos/o/r/contents/f.md --input body.json"
t DENY  "gh api graphql -f query='mutation{ x }'"
t DENY  "gh repo edit MirumeAI/PDM --visibility public"
t DENY  "gh release create v1.0.0"
t DENY  "gh secret set FOO --body bar"
t DENY  "gh workflow run deploy.yml"
t DENY  "gh pr merge 1 --admin"

echo "== ガード自身の書き換え =="
t DENY  ".claude/hooks/guard-main.sh"   "$WORK/on_feature" Edit
t DENY  ".claude/settings.json"         "$WORK/on_feature" Write

echo "== 通常の作業は通る =="
t ALLOW "git push origin feature/1-x"
t ALLOW "git push -u origin fix/2-y"
t ALLOW "git commit -m 'work'"
t ALLOW "git status"
t ALLOW "git log --oneline main"
t ALLOW "git diff main...HEAD"
t ALLOW "gh api repos/MirumeAI/PDM"
t ALLOW "gh pr create --base main --title x"
t ALLOW "gh pr list"
t ALLOW "gh issue view 1"
t ALLOW "ls -la"
t ALLOW "python3 scripts/ci/basic_checks.py"
t ALLOW "src/pdm/cli/test.py"           "$WORK/on_feature" Edit
t ALLOW "git switch -c feature/3-z"

echo
echo "合格 $PASS / 不合格 $FAIL"
[ "$FAIL" -eq 0 ]

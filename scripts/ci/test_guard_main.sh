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

# 検証する hook の場所。 **`CI_ROOT` があればそちらを見る。**
#
# 再利用 workflow から呼ぶとき、 この script は `.github` 側に置かれ、
# 検証したいのは **呼び出し側 Repository の hook** になる。 自分の場所から
# 辿るだけだと、 平台の hook を検証して「合格」と言ってしまう。
# `basic_checks.py` と `import_check.py` も同じ鍵を見ている。
_CI_ROOT="${CI_ROOT:-}"
if [ -n "$_CI_ROOT" ]; then
  HOOK="$(cd "$_CI_ROOT" && pwd)/.claude/hooks/guard-main.sh"
else
  HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/.claude/hooks/guard-main.sh"
fi
[ -f "$HOOK" ] || { echo "hook が見つかりません: $HOOK" >&2; exit 2; }
echo "検証する hook: $HOOK"

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
# 手元にあるタグ（タグの push・削除・付け替えの場面に使う。 この一時の Repository の中だけ）
git -C "$WORK/on_feature" tag v0.9.0

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

echo "== gh pr merge の CI ゲート =="
# 実際の Repository へ問い合わせるため、 ネットワークが無い環境では
# 「確認できない」で拒否されることを確かめる（分からないなら通さない）。
t DENY  "gh pr merge 99999 --squash"
t DENY  "gh pr merge --squash"

echo "== ガード自身の書き換え =="
t DENY  ".claude/hooks/guard-main.sh"   "$WORK/on_feature" Edit
t DENY  ".claude/settings.json"         "$WORK/on_feature" Write

echo "== タグは人が打つ（Product Release のタグ） =="
# 新しい Product Release のタグを打つ・消す・付け替えるのは Core の maintainer（人）だけ。
# Agent の作成・削除・付け替えと、 タグの push を止める。
t DENY  "git tag v1.2.3"
t DENY  "git tag -a v1.2.3 -m release"
t DENY  "git tag -s v1.2.3 -m release"
t DENY  "git tag -am release v1.2.3"
t DENY  "git tag -f v0.9.0 HEAD"
t DENY  "git tag -d v0.9.0"
t DENY  "git tag --delete v0.9.0"
t DENY  "git tag v1.2.3 HEAD~1"
t DENY  "git -C $WORK/on_feature tag v1.2.3"
t DENY  "bash -c 'git tag v1.2.3'"
t DENY  "git push origin --tags"
t DENY  "git push --follow-tags origin feature/1-x"
t DENY  "git push origin v0.9.0"
t DENY  "git push origin refs/tags/v1.2.3"
t DENY  "git push origin :refs/tags/v0.9.0"
t DENY  "git push origin :v0.9.0"
t DENY  "git push origin --delete v1.2.3"
t DENY  "git update-ref refs/tags/v0.9.0 HEAD"
# 並べ方・形の旗は一覧にしない（名前と並べると git はタグを作る）
t DENY  "git tag --sort=refname v1.2.3"
t DENY  "git tag --sort refname v1.2.3"
t DENY  "git tag -i v1.2.3"
t DENY  "git tag --format='%(refname)' v1.2.3"
t DENY  "git tag --column v1.2.3"
t DENY  "git tag --color v1.2.3"
t DENY  "git tag --omit-empty v1.2.3"
t DENY  "git tag --sort=-v:refname v1.2.3 HEAD~1"
# refspec の短い形・旗の略・設定による push
t DENY  "git push origin tags/v0.9.0"
t DENY  "git push origin --tag"
t DENY  "git push origin :tags/v0.9.0"
t DENY  "git push origin --del tags/v0.9.0"
t DENY  "git push origin --dele v1.2.3"
t DENY  "git -c push.followTags=true push origin feature/1-x"
t DENY  "git push --follow origin feature/1-x"
t DENY  "git push origin 'refs/*:refs/*'"
# 手元に無い LEGACY のタグ（案件の接頭辞つき・archive）の削除
t DENY  "git push origin --delete zz/v1.0.0"
t DENY  "git push origin :zz/v1.0.0"
t DENY  "git push origin --delete archive/zz"
# 確かめを通らない Release・タグの作成（gh）
t DENY  "gh release -R o/r create v1.2.3"
t DENY  "gh release new v1.2.3"
t DENY  "gh api repos/o/r/git/refs --field=ref=refs/tags/v1.2.3"
t DENY  "gh api repos/o/r/git/refs -fref=refs/tags/v1.2.3"
t DENY  "gh api repos/o/r/releases --raw-field=tag_name=v1.2.3"
t DENY  "gh api repos/o/r/git/refs -Fref=refs/tags/v1.2.3"
t DENY  "gh api -iXPOST repos/o/r/git/refs"
# 一括上書き・force の旗の略
t DENY  "git push --m origin"
t DENY  "git push --mirr origin"
t DENY  "git push --al origin"
t DENY  "git push --pru origin"
t DENY  "git push --forc origin feature/1-x"
t DENY  "git push --force-w origin feature/1-x"
t DENY  "git push --force-i origin feature/1-x"

echo "== 通常の作業は通る =="
t ALLOW "git tag -l 'v*' --sort=-v:refname"
t ALLOW "git tag --sort=-creatordate"
t ALLOW "git tag --format='%(refname:short)'"
t ALLOW "git tag -l --format='%(refname:short)' 'v0.4*'"
t ALLOW "git tag --column"
t ALLOW "git tag -n --sort=-v:refname"
t ALLOW "git tag --merged main v1.2.3"
t ALLOW "git push origin --delete v2-old-branch"
t ALLOW "gh api repos/o/r/git/refs/tags"
t ALLOW "gh api repos/o/r/releases --paginate"
t ALLOW "git push --atomic origin feature/1-x"
t ALLOW "git push --no-verify origin feature/1-x"
t ALLOW "git tag"
t ALLOW "git tag -l"
t ALLOW "git tag --list 'v*'"
t ALLOW "git tag -n"
t ALLOW "git tag --contains HEAD"
t ALLOW "git tag --points-at HEAD"
t ALLOW "git tag -v v0.9.0"
t ALLOW "git push origin --delete feature/old"
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

# **数字を作る側が、 文書との一致も確かめる。**
#
# 5 Repository の CLAUDE.md が「63 ケース」と書いていたが、 実行すると
# 65 ケース通る状態だった。 ケースを足したときに数字を直す仕組みが無く、
# 文章に書いた数字は実装が増えても変わらない。 人では気づけない。
#
# ここで見れば、 再利用 workflow を呼んでいる Repository は Pull Request
# ごとに止まる。 件数を書いていない Repository では何もしない。
_DOC_ROOT="${CI_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
_DOC="$_DOC_ROOT/CLAUDE.md"
if [ -f "$_DOC" ]; then
  _WRITTEN="$(grep -oE '[0-9]+ *ケース' "$_DOC" | head -1 | grep -oE '[0-9]+' || true)"
  if [ -n "$_WRITTEN" ] && [ "$_WRITTEN" != "$PASS" ]; then
    echo
    echo "不一致: CLAUDE.md は $_WRITTEN ケースと書いていますが、 実測は $PASS ケースです。" >&2
    echo "        ${_DOC#"$_DOC_ROOT"/} の件数を直してください。" >&2
    FAIL=$((FAIL+1))
  fi
fi

[ "$FAIL" -eq 0 ]

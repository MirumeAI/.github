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
# 作業 Branch の形でない Branch（LEGACY の長期 Branch に見立てた名前と demo。 案件の名前は使わない）と、
# ほかの作業 Branch の形（fix/・experiment/）
setup_repo "$WORK/on_legacy" legacy_line
setup_repo "$WORK/on_demo" demo
setup_repo "$WORK/on_fix" fix/2-y
setup_repo "$WORK/on_exp" experiment/3-z
# upstream がある作業 Branch（名前に / がある）と、 upstream が main の作業 Branch
setup_upstream() {
  local dir="$1" branch="$2" up="$3"
  setup_repo "$dir" "$branch"
  git -C "$dir" remote add origin https://example.invalid/repo.git   # 無いと upstream が解決できない
  git -C "$dir" update-ref "refs/remotes/origin/$up" HEAD
  git -C "$dir" config "branch.$branch.remote" origin
  git -C "$dir" config "branch.$branch.merge" "refs/heads/$up"
}
setup_upstream "$WORK/up_work" feature/4-w feature/4-w
setup_upstream "$WORK/up_main" feature/5-v main
# 手元にあるタグ（タグの push・削除・付け替えの場面に使う。 この一時の Repository の中だけ）
git -C "$WORK/on_feature" tag v0.9.0

run_hook() {
  local cmd="$1" cwd="$2" tool="${3:-Bash}"
  local payload
  payload="$(python3 -c '
import json,sys
t,c=sys.argv[1],sys.argv[2]
key={"NotebookEdit":"notebook_path"}.get(t, "file_path" if t in ("Edit","Write","MultiEdit") else "command")
print(json.dumps({"tool_name":t,"tool_input":{key:c}}))' "$tool" "$cmd")"
  ( cd "$cwd" && printf '%s' "$payload" | bash "$HOOK" >/dev/null 2>&1 )
  [ $? -eq 2 ] && echo DENY || echo ALLOW
}

# 落ちたときの表示で、 一時の作業場所の道を伏せる（CI の記録に手元の道を出さない）
_m() { local s="$1"; printf '%s' "${s//"$WORK"/<WORK>}"; }

t() {
  local exp="$1" cmd="$2" cwd="${3:-$WORK/on_feature}" tool="${4:-Bash}"
  local got; got="$(run_hook "$cmd" "$cwd" "$tool")"
  if [ "$got" = "$exp" ]; then PASS=$((PASS+1));
  else FAIL=$((FAIL+1)); printf '  NG  期待=%-5s 実際=%-5s  %s\n' "$exp" "$got" "$(_m "$cmd")"; fi
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

echo "== gh pr merge は GitHub Actions の結果で止めない =="
# Actions は merge の前提ではない（2026-10-19 の人の判断）。 check の結果を
# 問い合わせず、 保護の迂回（--admin。上の場面）だけを止める。
t ALLOW "gh pr merge 99999 --squash"
t ALLOW "gh pr merge --squash"

echo "== ガード自身の書き換え =="
t DENY  ".claude/hooks/guard-main.sh"   "$WORK/on_feature" Edit
t DENY  ".claude/settings.json"         "$WORK/on_feature" Write
# settings.local.json に disableAllHooks を書くと、個人設定のガードまで止まる
t DENY  ".claude/settings.local.json"   "$WORK/on_feature" Write
t DENY  "$WORK/on_feature/.claude/settings.local.json" "$WORK/on_feature" Edit
# 同じファイルを別の書き方で指しても止める（道は照らす前に正規化する）
t DENY  ".claude//settings.json"        "$WORK/on_feature" Write
t DENY  "$WORK/on_feature/.claude/./hooks/guard-main.sh" "$WORK/on_feature" Edit
t DENY  "$WORK/on_feature/.claude/hooks/../settings.json" "$WORK/on_feature" Write
t DENY  '.claude\settings.local.json'   "$WORK/on_feature" Write
t DENY  ".claude/Settings.json"         "$WORK/on_feature" Write
t DENY  ".claude/settings.json "        "$WORK/on_feature" Write
t DENY  ".claude./hooks/guard-main.sh"  "$WORK/on_feature" Edit
t DENY  ".claude/hooks/guard-main.sh"   "$WORK/on_feature" MultiEdit
t DENY  ".claude/hooks/x.ipynb"         "$WORK/on_feature" NotebookEdit
# ふつうのファイルは、 正規化しても止めない
t ALLOW "src/pdm/../pdm/cli/test.py"    "$WORK/on_feature" Edit
t ALLOW "docs/claude/settings.json"     "$WORK/on_feature" Write

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
# ---- 作業 Branch の形でない Branch はすべて守る（development-docs #220）----
t DENY  "git push origin --delete v2-old-branch"
t DENY  "git commit -m x"                    "$WORK/on_legacy"
t DENY  "git merge feature/1-x"              "$WORK/on_legacy"
t DENY  "git push"                           "$WORK/on_legacy"
t DENY  "git push origin HEAD"               "$WORK/on_legacy"
t DENY  "git push origin HEAD"               "$WORK/on_main"
t DENY  "git push origin legacy_line"
t DENY  "git push origin feature/1-x:legacy_line"
t DENY  "git push origin HEAD:refs/heads/legacy_line"
t DENY  "git push origin --delete legacy_line"
t DENY  "git push origin :legacy_line"
t DENY  "git push origin demo"
t DENY  "git commit -m x"                    "$WORK/on_demo"
t DENY  "git push --repo=origin main"
t DENY  "git push --repo origin main"
t DENY  "git push -o ci.skip origin main"
t DENY  "git push origin refs/notes/commits"
t DENY  "git push"                           "$WORK/up_main"
t DENY  "git branch -f legacy_line HEAD"
t DENY  "git branch -D legacy_line"
t DENY  "git branch -m legacy_line"
t DENY  "git branch -f main HEAD"
t DENY  "git update-ref refs/heads/legacy_line HEAD"
t DENY  "git update-ref -d refs/heads/main"
t ALLOW "git push origin HEAD"
t ALLOW "git push"                           "$WORK/up_work"
t ALLOW "git push origin HEAD:refs/heads/feature/1-x"
t ALLOW "git push -o ci.skip origin feature/1-x"
t ALLOW "git push --repo=origin feature/1-x"
t ALLOW "git push origin fix/2-y:fix/2-y"
t ALLOW "git push origin experiment/3-z"
t ALLOW "git commit -m x"                    "$WORK/on_fix"
t ALLOW "git commit -m x"                    "$WORK/on_exp"
t ALLOW "git branch -D feature/old"
t ALLOW "git branch -a"
t ALLOW "git branch --merged main"
t ALLOW "git branch -f feature/1-x HEAD"
t ALLOW "git update-ref refs/heads/feature/1-x HEAD"
t ALLOW "git pull --ff-only"                 "$WORK/on_main"
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

echo "== 判断できないときは拒否する（壊れた python3・古い bash・内部の失敗） =="
# 環境を変えて流す。 古い bash の実機は CI に無いので、 版を低く装う環境変数で確かめる
# （bash 4.3 の実機では、 直す前の hook で、 引数の無い `git push` の 3 件が落ちた。 2026-10-22）。
run_hook_env() {
  local envs="$1" cmd="$2" cwd="$3" tool="$4" payload
  payload="$(python3 -c '
import json,sys
t,c=sys.argv[1],sys.argv[2]
key={"NotebookEdit":"notebook_path"}.get(t, "file_path" if t in ("Edit","Write","MultiEdit") else "command")
print(json.dumps({"tool_name":t,"tool_input":{key:c}}))' "$tool" "$cmd")"
  ( cd "$cwd" && printf '%s' "$payload" | env "$envs" bash "$HOOK" >/dev/null 2>&1 )
  [ $? -eq 2 ] && echo DENY || echo ALLOW
}
te() {
  local exp="$1" envs="$2" cmd="$3" cwd="${4:-$WORK/on_feature}" tool="${5:-Bash}"
  local got; got="$(run_hook_env "$envs" "$cmd" "$cwd" "$tool")"
  if [ "$got" = "$exp" ]; then PASS=$((PASS+1));
  else FAIL=$((FAIL+1)); printf '  NG  期待=%-5s 実際=%-5s  [%s] %s\n' "$exp" "$got" \
         "$(_m "$envs" | sed -E 's#^PATH=[^:]*/(py_[a-z0-9]+):.*#PATH=<\1>:...#')" "$(_m "$cmd")"; fi
}
REAL_PY="$(command -v python3)"
mkdir -p "$WORK/py_fail" "$WORK/py_silent" "$WORK/py_seg" "$WORK/py_seg0" "$WORK/py_xutf"
printf '#!/bin/sh\nexit 1\n' > "$WORK/py_fail/python3"                    # 落ちる
printf '#!/bin/sh\nexit 0\n' > "$WORK/py_silent/python3"                  # 何も出さない
# 2 回目の呼び出しは、 引数に「-」（標準入力の script）があることで見分ける
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = "-" ] && exit 1; done\nexec "%s" "$@"\n' "$REAL_PY" > "$WORK/py_seg/python3"   # 2 回目だけ落ちる
printf '#!/bin/sh\nfor a in "$@"; do [ "$a" = "-" ] && exit 0; done\nexec "%s" "$@"\n' "$REAL_PY" > "$WORK/py_seg0/python3"  # 2 回目だけ何も出さない
# 入出力を UTF-8 にそろえる指定（-X utf8）が無ければ落ちる。 この機械の文字コードは UTF-8 なので、
# 指定が無くても本物の python3 では判定が変わらず、 指定を外したことに気づけないため
printf '#!/bin/sh\np=\nfor a in "$@"; do [ "$p" = "-X" ] && [ "$a" = "utf8" ] && exec "%s" "$@"; p="$a"; done\nexit 1\n' "$REAL_PY" > "$WORK/py_xutf/python3"
chmod +x "$WORK/py_fail/python3" "$WORK/py_silent/python3" "$WORK/py_seg/python3" "$WORK/py_seg0/python3" "$WORK/py_xutf/python3"
te DENY  "PATH=$WORK/py_fail:$PATH"            "git push origin main"
te DENY  "PATH=$WORK/py_fail:$PATH"            "git status"
te DENY  "PATH=$WORK/py_silent:$PATH"          "git status"
te DENY  "PATH=$WORK/py_silent:$PATH"          "src/pdm/cli/test.py" "$WORK/on_feature" Edit
te DENY  "PATH=$WORK/py_seg:$PATH"             "git push origin main"
te DENY  "PATH=$WORK/py_seg0:$PATH"            "git push origin main"
# ふだんは通る命令でも拒否する（偽の python3 が効かなくなったら、 ここで気づく）
te DENY  "PATH=$WORK/py_seg:$PATH"             "git status"
te DENY  "PATH=$WORK/py_seg0:$PATH"            "git status"
te ALLOW "PATH=$WORK/py_xutf:$PATH"            "git status"
te DENY  "MIRUME_GUARD_ASSUME_BASH=4.3"        "git status"
te DENY  "MIRUME_GUARD_ASSUME_BASH=3.2"        "git status"
te ALLOW "MIRUME_GUARD_ASSUME_BASH=9.9"        "git status"
te DENY  "MIRUME_GUARD_TEST_INTERNAL_ERROR=1"  "git status"
# 入出力の文字コードが UTF-8 でない環境（Windows の日本語の環境の cp932 など）でも、 同じ判定にする
te ALLOW "PYTHONIOENCODING=cp932"              "echo 'a — b'"
te DENY  "PYTHONIOENCODING=cp932"              "echo 'a — b'; git push origin main"
# 作業の場所や PYTHONPATH に、 標準と同じ名前の module（ここでは読むと落ちる json.py と shlex.py。
# 1 回目と 2 回目の python3 がそれぞれ読む）があっても、 それを読まずに同じ判定にする
# （読むと、 判断できずにすべてを拒否してしまう）
setup_repo "$WORK/on_feature_mod" feature/1-x
mkdir -p "$WORK/mods"
for m in json shlex; do
  printf 'raise ImportError("x")\n' > "$WORK/on_feature_mod/$m.py"
  printf 'raise ImportError("x")\n' > "$WORK/mods/$m.py"
done
t ALLOW "git status"                           "$WORK/on_feature_mod"
t DENY  "git push origin main"                 "$WORK/on_feature_mod"
te ALLOW "PYTHONPATH=$WORK/mods"               "git status"
te DENY  "PYTHONPATH=$WORK/mods"               "git push origin main"
# 直す前は、 決まった印で始まる Bash の命令を別の種類の入力として素通りさせていた（印の確かめで塞いだ）
t DENY  "@@FILE@@x; git push origin main"
t DENY  $'@@FILE@@x\ngit push origin main'
t DENY  "@@FILE@@x; git push --force origin feature/1-x"

echo "== Repository の設定のフックは、 ガードを動かせないときに拒否する =="
# .claude/settings.json のガードのフックを、 書かれた命令のまま sh で流す。 Claude Code は
# 終了コード 2 だけを拒否として扱うので、 ガードのファイルが無い・bash が無いときも 2 で
# 終わらないと、 操作が通ってしまう。
_ROOT="$(cd "$(dirname "$HOOK")/../.." && pwd)"
mkdir -p "$WORK/no_guard"
run_settings_hook() {
  local cmd="$1" projdir="$2" tool="$3" value="$4" payload
  payload="$(python3 -c '
import json,sys
t,c=sys.argv[1],sys.argv[2]
key="file_path" if t != "Bash" else "command"
print(json.dumps({"tool_name":t,"tool_input":{key:c}}))' "$tool" "$value")"
  ( cd "$WORK/on_feature" && printf '%s' "$payload" | CLAUDE_PROJECT_DIR="$projdir" sh -c "$cmd" >/dev/null 2>&1 )
  [ $? -eq 2 ] && echo DENY || echo ALLOW
}
ts() {
  local exp="$1" label="$2"; shift 2
  local got; got="$(run_settings_hook "$@")"
  if [ "$got" = "$exp" ]; then PASS=$((PASS+1));
  else FAIL=$((FAIL+1)); printf '  NG  期待=%-5s 実際=%-5s  [.claude/settings.json] %s\n' "$exp" "$got" "$label"; fi
}
_HOOKS="$(python3 -c '
import json,sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
for e in ((d.get("hooks") or {}).get("PreToolUse") or []):
    for h in (e.get("hooks") or []):
        c = str(h.get("command") or "")
        if "guard-main.sh" in c:
            print(str(e.get("matcher") or "") + "\t" + c)' "$_ROOT/.claude/settings.json")"
_NB=0; _NE=0
while IFS=$'\t' read -r _mt _cmd; do
  [ -n "$_cmd" ] || continue
  case "$_mt" in
    Bash)
      _NB=$((_NB+1))
      ts ALLOW "Bash: ふつうの命令"            "$_cmd" "$_ROOT" Bash "ls"
      ts DENY  "Bash: main への push"          "$_cmd" "$_ROOT" Bash "git push origin main"
      ts DENY  "Bash: ガードのファイルが無い"   "$_cmd" "$WORK/no_guard" Bash "ls" ;;
    *Edit*)
      _NE=$((_NE+1))
      ts ALLOW "$_mt: ふつうのファイル"         "$_cmd" "$_ROOT" Edit "src/a.py"
      ts DENY  "$_mt: ガードの書き換え"         "$_cmd" "$_ROOT" Edit ".claude/hooks/guard-main.sh"
      ts DENY  "$_mt: ガードのファイルが無い"   "$_cmd" "$WORK/no_guard" Edit "src/a.py" ;;
  esac
done <<< "$_HOOKS"
# Bash と Edit のガードのフックが、 それぞれ 1 つずつある
if [ "$_NB" = 1 ] && [ "$_NE" = 1 ]; then PASS=$((PASS+1));
else FAIL=$((FAIL+1)); printf '  NG  .claude/settings.json のガードのフックの数（Bash %s・Edit %s。どちらも 1 のはず。ファイルが無いときも 0）\n' "$_NB" "$_NE"; fi

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

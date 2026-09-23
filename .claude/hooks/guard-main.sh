#!/usr/bin/env bash
# 保護 Branch への直接 commit / push を拒否する PreToolUse hook。
#
# 標準入力で Claude Code から JSON を受け取り、 拒否する場合は終了コード 2 と
# 標準エラーの理由を返す。
#
# **設計の前提: ここは「うっかり」を止める壁であって、 敵対的な回避を
# 完全に塞ぐものではない。** ターミナルから直接叩けば素通りする。
# それでも、 書き方を少し変えただけで抜けることは無いようにする。
#
# 判定の考え方:
#   - 先頭の飾り (env / command / bash -c / 絶対パス / VAR=val / 括弧) を剥がす
#   - `;` `&&` `||` `|` と改行で区切って、 1つずつ見る
#   - `git -C <dir>` と `cd <dir> &&` を追って、 **どの Repository に対する
#     操作か**を特定してから Branch を調べる
#   - 判断できないときは **拒否側へ倒す** (通してしまうより止めるほうが安い)
#
# 保護する Branch は MIRUME_PROTECTED_BRANCH で変えられる (既定 main)。

set -uo pipefail

PROTECTED="${MIRUME_PROTECTED_BRANCH:-}"
[ -n "$PROTECTED" ] || PROTECTED="main"

deny() {
  echo "MirumeAI 開発ルールにより拒否しました: $1" >&2
  [ -n "${2:-}" ] && echo "$2" >&2
  echo "Issue 番号付きの作業 Branch を作り、 Pull Request 経由で変更してください。" >&2
  exit 2
}

# ---------------------------------------------------------------- 入力
# python3 が無い環境では判定できない。 **素通しせず拒否する。**
# 通してしまうと、 ガードが無いことに誰も気づかないまま進んでしまう。
if ! command -v python3 >/dev/null 2>&1; then
  deny "python3 が無く、 コマンドを検査できない" \
       "python3 を入れるか、 この操作を手元のターミナルで行ってください。"
fi

CMD="$(python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("@@PARSE_ERROR@@"); sys.exit(0)
ti = d.get("tool_input") or {}
name = d.get("tool_name") or ""
if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    print("@@FILE@@" + str(ti.get("file_path") or ""))
else:
    print(ti.get("command") or "")
' 2>/dev/null)"

case "$CMD" in
  "@@PARSE_ERROR@@") deny "入力を解釈できない" "" ;;
esac

# ------------------------------------------------- ファイル編集の保護
# **Claude が自分のガードを書き換えられないようにする。**
# 変更が必要なときは人が確認する。
case "$CMD" in
  "@@FILE@@"*)
    f="${CMD#@@FILE@@}"
    case "$f" in
      */.claude/hooks/*|*/.claude/settings.json|.claude/hooks/*|.claude/settings.json)
        deny "ガード設定の書き換え: $f" \
             "この操作は人が内容を確認したうえで行ってください。" ;;
    esac
    exit 0 ;;
esac

[ -n "$CMD" ] || exit 0

# ------------------------------------------------------------ 前処理
# 飾りを剥がし、 1行1コマンドへ分解する。
SEGMENTS="$(python3 - "$CMD" <<'PY'
import re, shlex, sys

raw = sys.argv[1]

# bash -c '...' / sh -c "..." / eval '...' の中身を取り出す (入れ子も追う)
def unwrap(text, depth=0):
    if depth > 4:
        return [text]
    out = []
    m = re.search(r'\b(?:ba|z|da|k)?sh\s+-[a-z]*c\s+(.+)$|(?<![\w-])eval\s+(.+)$', text)
    if m:
        inner = m.group(1) or m.group(2)
        try:
            parts = shlex.split(inner)
            if parts:
                out.extend(unwrap(parts[0], depth + 1))
        except ValueError:
            out.append(inner)
        out.append(text[:m.start()])
        return out
    return [text]

chunks = []
for t in unwrap(raw):
    # 括弧・波括弧を外す
    t = re.sub(r'[(){}]', ' ', t)
    # 区切りで分解
    chunks.extend(re.split(r'\s*(?:\|\||&&|[;|&]|\n)\s*', t))

for c in chunks:
    c = c.strip()
    if not c:
        continue
    try:
        toks = shlex.split(c)
    except ValueError:
        toks = c.split()
    # 先頭の飾りを剥がす: VAR=val / env / command / builtin / \ / 絶対パス / sudo / nohup / time / xargs
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*=.*', t):
            i += 1; continue
        base = t.lstrip('\\').rsplit('/', 1)[-1]
        if base in ('env', 'command', 'builtin', 'sudo', 'nohup', 'time', 'xargs',
                    'stdbuf', 'nice', 'setsid', 'exec'):
            i += 1
            # env の -i や -u NAME を飛ばす
            while i < len(toks) and toks[i].startswith('-'):
                if toks[i] in ('-u',) and i + 1 < len(toks):
                    i += 2
                else:
                    i += 1
            continue
        break
    toks = toks[i:]
    if not toks:
        continue
    toks[0] = toks[0].lstrip('\\').rsplit('/', 1)[-1]
    print('\x1f'.join(toks))
PY
)"

# cd で移動した先を追う（`cd X && git push` に効かせる）
CWD_OVERRIDE=""
while IFS= read -r line; do
  [ -n "$line" ] || continue
  IFS=$'\x1f' read -r -a TOK <<< "$line"
  prog="${TOK[0]:-}"

  # ---- cd の追跡 ----
  if [ "$prog" = "cd" ] && [ -n "${TOK[1]:-}" ]; then
    CWD_OVERRIDE="${TOK[1]}"
    continue
  fi

  case "$prog" in
    git) ;;
    gh)  ;;
    *) continue ;;
  esac

  # ---- 対象 Repository を決める ----
  TARGET="${CWD_OVERRIDE:-.}"
  ARGS=()
  i=1
  while [ $i -lt ${#TOK[@]} ]; do
    t="${TOK[$i]}"
    case "$t" in
      -C) TARGET="${TOK[$((i+1))]:-.}"; i=$((i+2)); continue ;;
      -c) i=$((i+2)); continue ;;          # git -c key=val
      --git-dir=*|--work-tree=*) i=$((i+1)); continue ;;
      -*) if [ "$prog" = "git" ] && [ ${#ARGS[@]} -eq 0 ]; then i=$((i+1)); continue; fi ;;
    esac
    ARGS+=("$t"); i=$((i+1))
  done
  sub="${ARGS[0]:-}"
  rest=("${ARGS[@]:1}")
  joined=" ${rest[*]} "

  cur_branch() { git -C "$TARGET" rev-parse --abbrev-ref HEAD 2>/dev/null || echo ""; }
  upstream()   { git -C "$TARGET" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || echo ""; }

  if [ "$prog" = "git" ]; then
    case "$sub" in
      commit|merge|rebase|cherry-pick|revert|am)
        if [ "$(cur_branch)" = "$PROTECTED" ]; then
          deny "保護Branch '$PROTECTED' 上での $sub" "対象: $TARGET"
        fi ;;
      push)
        # force / 全体上書き / 削除 は refspec を問わず拒否
        for a in "${rest[@]}"; do
          case "$a" in
            --force|--force=*|--force-with-lease|--force-with-lease=*|--force-if-includes|--mirror|--all|--prune)
              deny "force push または一括上書き ($a)" "対象: $TARGET" ;;
            -*f*) case "$a" in --*) ;; *) deny "force push ($a)" "対象: $TARGET" ;; esac ;;
            --delete|-d) DEL=1 ;;
            +*) deny "force push (refspec の先頭 +: $a)" "対象: $TARGET" ;;
          esac
        done
        # 保護 Branch を名指ししていないか（refs/heads/ や src:dst も見る）
        for a in "${rest[@]}"; do
          case "$a" in -*) continue ;; esac
          dst="${a##*:}"; dst="${dst#refs/heads/}"; dst="${dst#+}"
          src="${a%%:*}"; src="${src#refs/heads/}"; src="${src#+}"
          if [ "$dst" = "$PROTECTED" ] || { [ "$a" = "$PROTECTED" ]; }; then
            if [ "${DEL:-0}" = "1" ]; then
              deny "保護Branch '$PROTECTED' の削除" "対象: $TARGET"
            fi
            deny "保護Branch '$PROTECTED' への push" "対象: $TARGET / 指定: $a"
          fi
          # `:main` 形式の削除
          if [ -z "$src" ] && [ "$dst" = "$PROTECTED" ]; then
            deny "保護Branch '$PROTECTED' の削除" "対象: $TARGET"
          fi
        done
        # refspec を明示していない場合は現在 Branch と upstream を見る
        has_ref=0
        for a in "${rest[@]}"; do
          case "$a" in -*) continue ;; esac
          # リモート名/URL の次に来るものを refspec とみなす
          case "$a" in *://*|git@*) continue ;; esac
          if [ "$a" != "origin" ] && [ -n "$a" ]; then has_ref=1; fi
        done
        if [ "$has_ref" = "0" ]; then
          c="$(cur_branch)"; u="$(upstream)"
          if [ "$c" = "$PROTECTED" ] || [ "${u##*/}" = "$PROTECTED" ]; then
            deny "保護Branch '$PROTECTED' への push" "対象: $TARGET / 現在: ${c:-不明} / upstream: ${u:-未設定}"
          fi
        fi ;;
      update-ref|branch)
        case "$joined" in
          *" -f "*|*" --force "*|*"$PROTECTED"*)
            case "$joined" in
              *"$PROTECTED"*) deny "保護Branch '$PROTECTED' の付け替え ($sub)" "対象: $TARGET" ;;
            esac ;;
        esac ;;
    esac
  fi

  if [ "$prog" = "gh" ]; then
    case "$sub" in
      api)
        for a in "${rest[@]}"; do
          case "$a" in
            -X*|--method*|-f|--field|-F|--raw-field|--input)
              deny "gh api による書き込み ($a)" \
                   "Repository の内容や設定を API で直接変更しないでください。" ;;
          esac
        done
        case "$joined" in
          *mutation*) deny "gh api graphql の mutation" "" ;;
        esac ;;
      repo)
        case "${rest[0]:-}" in
          edit|delete|archive|rename|set-default)
            deny "gh repo ${rest[0]} による Repository 設定の変更" \
                 "Visibility やアーカイブの変更は人が行ってください。" ;;
        esac ;;
      secret|variable|ruleset)
        deny "gh $sub による設定の変更" "" ;;
      release)
        case "${rest[0]:-}" in
          create|delete|edit|upload) deny "gh release ${rest[0]}" "" ;;
        esac ;;
      workflow)
        case "${rest[0]:-}" in
          run|enable|disable) deny "gh workflow ${rest[0]}" "" ;;
        esac ;;
      pr)
        case "$joined" in
          *" --admin "*) deny "gh pr merge --admin による保護の迂回" "" ;;
        esac
        # ---- CI が緑でない Pull Request は merge させない ----
        #   GitHub Free かつ Private Repository では Required status checks を
        #   設定できない。 **赤いまま merge できてしまう**ので、 ここで止める。
        #   CI の結果が取れないときも止める（分からないなら通さない）。
        if [ "${rest[0]:-}" = "merge" ]; then
          pr_num=""
          for a in "${rest[@]:1}"; do
            case "$a" in -*) continue ;; esac
            case "$a" in *[!0-9]*) ;; *) pr_num="$a"; break ;; esac
          done
          if [ -z "$pr_num" ]; then
            pr_num="$(git -C "$TARGET" rev-parse --abbrev-ref HEAD 2>/dev/null)"
          fi
          rollup="$(gh pr view "$pr_num" --repo "$(git -C "$TARGET" remote get-url origin 2>/dev/null | sed -E 's#.*github\.com[:/]##; s#\.git$##')" \
                      --json statusCheckRollup 2>/dev/null \
                    | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("UNKNOWN"); raise SystemExit
rows = d.get("statusCheckRollup") or []
if not rows:
    print("NONE"); raise SystemExit
bad = [r.get("name") or r.get("context") or "?"
       for r in rows
       if (r.get("conclusion") or r.get("state") or "").upper() not in ("SUCCESS", "NEUTRAL", "SKIPPED")]
print("BAD:" + ",".join(bad) if bad else "GREEN")
' 2>/dev/null)"
          case "$rollup" in
            GREEN) ;;
            BAD:*) deny "CI が緑ではない Pull Request の merge" \
                        "失敗または未完了: ${rollup#BAD:}" ;;
            NONE)  deny "CI の結果が無い Pull Request の merge" \
                        "workflow が動いていません。 手元で scripts/ci/ の検査を通してください。" ;;
            *)     deny "CI の結果を確認できない" \
                        "gh で Pull Request の状態を取得できませんでした。" ;;
          esac
        fi ;;
    esac
  fi
done <<< "$SEGMENTS"

exit 0

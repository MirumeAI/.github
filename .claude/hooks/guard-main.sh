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
#   - 判断できないときは **拒否側へ倒す** (通してしまうより止めるほうが安い)。
#     古い bash・python3 の失敗・途中で落ちたときも、 黙って通さず拒否する
#   - **Product Release のタグは人（Core の maintainer）が打つ。** Agent の
#     `git tag` の作成・削除・付け替えと、 タグの push・削除を止める
#     （一覧と確かめは通す。 人が自分のターミナルで打つタグは止めない）
#
# 保護する Branch は MIRUME_PROTECTED_BRANCH で変えられる (既定 main)。

set -uo pipefail

# **判断できないまま終わったら、 拒否する。** 決まった終わり方は 0（通す）と 2（拒否）だけ。
# それ以外（未定義の変数で落ちた・python3 が落ちた など）で終わると、 Claude Code は
# 2 以外を「拒否ではない」として通してしまう（bash 4.3 で、 引数の無い `git push` が
# 終了コード 1 で落ちて通ることを確かめた。 2026-10-22）。
trap '_rc=$?; if [ "$_rc" -ne 0 ] && [ "$_rc" -ne 2 ]; then
  echo "MirumeAI 開発ルールにより拒否しました: ガードが判断できないまま終わった（終了コード $_rc）" >&2
  exit 2
fi' EXIT

PROTECTED="${MIRUME_PROTECTED_BRANCH:-}"
[ -n "$PROTECTED" ] || PROTECTED="main"

deny() {
  echo "MirumeAI 開発ルールにより拒否しました: $1" >&2
  [ -n "${2:-}" ] && echo "$2" >&2
  echo "Issue 番号付きの作業 Branch を作り、 Pull Request 経由で変更してください。" >&2
  exit 2
}

# **bash 4.4 より古いと、 判定の途中で落ちる**（空の配列と set -u。 macOS の既定は 3.2）。
# 落ちても上の trap で拒否になるが、 何が足りないかを言ってから止める。
# 試験では MIRUME_GUARD_ASSUME_BASH（例 4.3）で古い版を装える。 **低くする向きにだけ効く**
# （高い版を書いても、 本当の版より新しくはならない）。
_bv_major="${BASH_VERSINFO[0]}"; _bv_minor="${BASH_VERSINFO[1]}"
case "${MIRUME_GUARD_ASSUME_BASH:-}" in
  [0-9]*.[0-9]*)
    _am="${MIRUME_GUARD_ASSUME_BASH%%.*}"; _an="${MIRUME_GUARD_ASSUME_BASH#*.}"; _an="${_an%%.*}"
    case "$_am$_an" in
      *[!0-9]*) ;;
      *) if [ "$_am" -lt "$_bv_major" ] || { [ "$_am" -eq "$_bv_major" ] && [ "$_an" -lt "$_bv_minor" ]; }; then
           _bv_major="$_am"; _bv_minor="$_an"
         fi ;;
    esac ;;
esac
if [ "$_bv_major" -lt 4 ] || { [ "$_bv_major" -eq 4 ] && [ "$_bv_minor" -lt 4 ]; }; then
  deny "bash $_bv_major.$_bv_minor ではコマンドを正しく検査できない（4.4 以上が要る）" \
       "新しい bash を PATH の先に置いてください（macOS では Homebrew の bash）。"
fi

# 試験のためだけの入口: 内部の失敗を起こし、 上の trap が拒否に替えることを確かめる。
# **拒否の向きにしか効かない。**
[ -z "${MIRUME_GUARD_TEST_INTERNAL_ERROR:-}" ] || exit 3

TAG_HINT_NEW="新しい Product Release のタグは、 Core の maintainer（人）が自分のターミナルで打ちます。 Agent は命令の案と確かめだけを出してください。"
TAG_HINT_KEEP="タグは消さない・動かさない（LEGACY を含む）。 Agent は、 タグの push・削除・付け替えの命令も、 その案も出しません。"
RELEASE_HINT="GitHub Release は、 確かめに合格したタグから CI が作ります。 手で作らない・変えない・消さない。"

# $1 が $2 の略（$3 文字以上の頭）か。 git は紛れの無い旗の略を受け付ける（--tag・--del など）
prefix_of() { [ "${#1}" -ge "$3" ] && [ "${2#"$1"}" != "$2" ]; }

# ---------------------------------------------------------------- 入力
# python3 が無い環境では判定できない。 **素通しせず拒否する。**
# 通してしまうと、 ガードが無いことに誰も気づかないまま進んでしまう。
if ! command -v python3 >/dev/null 2>&1; then
  deny "python3 が無く、 コマンドを検査できない" \
       "python3 を入れるか、 この操作を手元のターミナルで行ってください。"
fi

# python3 は -I で起動する。 作業の場所の module と PYTHON* の環境変数を読まない
# （作業の場所に標準と同じ名前のファイル、 たとえば json.py があると、 それを読んで判定が変わる）。
# 入出力は -X utf8 で UTF-8 にそろえる（環境の文字コードが cp932 などだと、「—」のような
# 文字で落ちて、 正しい命令まで拒否してしまう。 -I なので PYTHONIOENCODING は効かない）。
CMD="$(python3 -I -X utf8 -c '
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
    print("@@CMD@@" + str(ti.get("command") or ""))
' 2>/dev/null)" || deny "python3 で入力を読めない" "python3 -c 'print(1)' が動くかを確かめてください。"

# **python3 が在っても、 動いているとは限らない**（例: Windows の Store の代わりの python3 は
# 何も出さずに終わる）。 決まった印で始まる出力のときだけ先へ進む。
case "$CMD" in
  "@@PARSE_ERROR@@") deny "入力を解釈できない" "" ;;
  "@@FILE@@"*|"@@CMD@@"*) ;;
  *) deny "python3 の出力を読めない（python3 が正しく動いていない）" "python3 -c 'print(1)' が動くかを確かめてください。" ;;
esac

# ------------------------------------------------- ファイル編集の保護
# **Claude の Edit / Write で、ガード（.claude/hooks/）と settings.json・settings.local.json を書き換えさせない。**
# settings.local.json に disableAllHooks を書くと、個人設定のガードまで止まるため、同じく守る。
# ガードの変更は MirumeAI/.github の Pull Request で案を出し、人の承認（Human Adjudication）と
# merge の後に、正本（.github の main）から写して同期する。人が手でファイルを写す必要はない。
case "$CMD" in
  "@@FILE@@"*)
    f="${CMD#@@FILE@@}"
    case "$f" in
      */.claude/hooks/*|*/.claude/settings.json|*/.claude/settings.local.json|.claude/hooks/*|.claude/settings.json|.claude/settings.local.json)
        deny "ガード設定の書き換え: $f" \
             "ガードの変更は MirumeAI/.github の Pull Request で案を出し、人の承認と merge の後に正本から写して同期してください。" ;;
    esac
    exit 0 ;;
esac

CMD="${CMD#@@CMD@@}"
[ -n "$CMD" ] || exit 0

# ------------------------------------------------------------ 前処理
# 飾りを剥がし、 1行1コマンドへ分解する。
SEGMENTS="$(python3 -I -X utf8 - "$CMD" <<'PY'
import re, shlex, sys

print('@@SEG@@')

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
)" || deny "python3 で命令を分けられない" "python3 -c 'print(1)' が動くかを確かめてください。"

case "$SEGMENTS" in
  "@@SEG@@"*) SEGMENTS="${SEGMENTS#@@SEG@@}" ;;
  *) deny "python3 の出力を読めない（命令を分けられない）" "python3 -c 'print(1)' が動くかを確かめてください。" ;;
esac

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
      -c)                                    # git -c key=val
        case "$(printf '%s' "${TOK[$((i+1))]:-}" | tr '[:upper:]' '[:lower:]')" in
          push.followtags*) deny "タグの push (git -c push.followTags)" "$TAG_HINT_KEEP" ;;
        esac
        i=$((i+2)); continue ;;
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
        # 旗の略（--m・--mirr・--al・--pru・--forc・--force-w など）も止める。
        # git は紛れの無い長い旗の略を受け付ける。 --mirror の略は全部のタグも送る
        for a in "${rest[@]}"; do
          case "$a" in
            --*)
              nm="${a%%=*}"
              if prefix_of "$nm" --mirror 3 || prefix_of "$nm" --all 4 || prefix_of "$nm" --prune 5 \
                 || prefix_of "$nm" --force 5 || prefix_of "$nm" --force-with-lease 9 \
                 || prefix_of "$nm" --force-if-includes 9; then
                deny "force push または一括上書き ($a)" "対象: $TARGET"
              fi ;;
          esac
        done
        # ---- タグの push・削除は人が行う（Product Release のタグ）----
        #   名前だけの refspec は、 手元のタグを指していれば止める。 削除で、
        #   版のタグの形（v の後に数字）のものは、 手元に無くても止める。
        #   旗の略（--tag・--follow・--del など）と、 refspec の短い形
        #   （tags/<名前>・:tags/<名前>）も見る。 削除で、 版のタグの形
        #   （v数字.数字…・<接頭辞>/v数字.数字…・archive/…）のものは、
        #   手元に無くても止める。
        tag_del=0
        for a in "${rest[@]}"; do
          case "$a" in
            --*)
              if prefix_of "$a" --tags 4 || prefix_of "$a" --follow-tags 5; then
                deny "タグの push ($a)" "$TAG_HINT_KEEP"
              fi
              if prefix_of "$a" --delete 4; then tag_del=1; fi ;;
            -*d*) tag_del=1 ;;
          esac
        done
        for a in "${rest[@]}"; do
          case "$a" in -*|*://*|git@*) continue ;; esac
          tsrc="${a%%:*}"; tdst="${a##*:}"; tsrc="${tsrc#+}"; tdst="${tdst#+}"
          for n in "$tsrc" "$tdst"; do
            [ -n "$n" ] || continue
            case "$n" in
              refs/tags/*|tags/*) deny "タグの push・削除 ($n)" "$TAG_HINT_KEEP" ;;
              # ワイルドカードの refspec（refs/*:refs/* など）は、 手元のタグや保護 Branch も送る
              *'*'*) deny "ワイルドカードの refspec の push ($n)" "$TAG_HINT_KEEP" ;;
            esac
            if git -C "$TARGET" show-ref --verify --quiet "refs/tags/$n" 2>/dev/null; then
              deny "タグの push・削除 ($n)" "$TAG_HINT_KEEP"
            fi
          done
          is_del="$tag_del"
          case "$a" in :*) is_del=1 ;; esac
          if [ "$is_del" = "1" ]; then
            case "$tdst" in
              v[0-9]*.[0-9]*|*/v[0-9]*.[0-9]*|archive/*)
                deny "タグの削除 ($tdst)" "$TAG_HINT_KEEP" ;;
            esac
          fi
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
      tag)
        # ---- タグは人が打つ（Product Release のタグ）----
        #   名前を付けてよいのは、 一覧（-l / --list / -n / --contains /
        #   --no-contains / --points-at / --merged / --no-merged）と、 確かめ
        #   （-v / --verify）のときだけ。 並べ方・形の旗（--sort / --format / -i /
        #   --column / --color / --omit-empty）は一覧にしないので、 名前と並べると
        #   git はタグを作る。 作る・消す・付け替える旗と、 知らない旗は止める。
        tag_list=0; tag_write=""; tag_pos=0; skip_next=0
        for a in "${rest[@]}"; do
          if [ "$skip_next" = "1" ]; then skip_next=0; continue; fi
          case "$a" in
            -l|--list|-v|--verify|-n|-n[0-9]*|--contains=*|--no-contains=*|--points-at=*|--merged=*|--no-merged=*)
              tag_list=1 ;;
            --contains|--no-contains|--points-at|--merged|--no-merged)
              tag_list=1; skip_next=1 ;;
            --sort=*|--format=*|--column|--column=*|--no-column|--color|--color=*|--no-color|-i|--ignore-case|--omit-empty)
              ;;
            --sort|--format)
              skip_next=1 ;;
            --) ;;
            --*) tag_write="$a" ;;
            -*)
              case "$a" in
                *[asumFfde]*) tag_write="$a" ;;
                *[lvn]*) tag_list=1 ;;
                *) tag_write="$a" ;;
              esac ;;
            *) tag_pos=$((tag_pos+1)) ;;
          esac
        done
        if [ -n "$tag_write" ]; then
          case "$tag_write" in
            --delete|--force|-*[df]*) deny "タグの削除・付け替え (git tag $tag_write)" "$TAG_HINT_KEEP" ;;
            *) deny "タグの作成 (git tag $tag_write)" "$TAG_HINT_NEW" ;;
          esac
        fi
        if [ "$tag_pos" -gt 0 ] && [ "$tag_list" = "0" ]; then
          deny "タグの作成 (git tag <名前>)" "$TAG_HINT_NEW"
        fi ;;
      update-ref|branch)
        if [ "$sub" = "update-ref" ]; then
          case "$joined" in
            *refs/tags/*) deny "タグの付け替え・削除 (git update-ref)" "$TAG_HINT_KEEP" ;;
          esac
        fi
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
            -X*|--method*|-f*|-F*|--field*|--raw-field*|--input*)
              deny "gh api による書き込み ($a)" \
                   "Repository の内容や設定を API で直接変更しないでください。" ;;
            --*) ;;
            -*)
              # 短い旗のまとまり（-iXPOST など）の頭の英字に X・f・F があれば止める
              lead="${a#-}"; lead="${lead%%[!A-Za-z]*}"
              case "$lead" in
                *[XfF]*) deny "gh api による書き込み ($a)" \
                           "Repository の内容や設定を API で直接変更しないでください。" ;;
              esac ;;
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
        # 子命令の前に -R / --repo を置く形と、 作成の別名（new）も見る
        rsub=""; rskip=0
        for a in "${rest[@]}"; do
          if [ "$rskip" = "1" ]; then rskip=0; continue; fi
          case "$a" in
            -R|--repo) rskip=1 ;;
            -*) ;;
            *) rsub="$a"; break ;;
          esac
        done
        case "$rsub" in
          create|new|delete|delete-asset|edit|upload) deny "gh release $rsub" "$RELEASE_HINT" ;;
        esac ;;
      workflow)
        case "${rest[0]:-}" in
          run|enable|disable) deny "gh workflow ${rest[0]}" "" ;;
        esac ;;
      pr)
        case "$joined" in
          *" --admin "*) deny "gh pr merge --admin による保護の迂回" "" ;;
        esac
        # GitHub Actions の check の結果では merge を止めない（2026-10-19 の人の判断）。
        #   Actions は merge の前提ではない（動かないときもある）。 merge の条件
        #   （exact head・Required Verification・Reviewer）は決まりで守り、
        #   ここでは保護の迂回（--admin）だけを止める。
        ;;
    esac
  fi
done <<< "$SEGMENTS"

exit 0

#!/usr/bin/env bash
# main への直接 commit / push を拒否する PreToolUse hook
#
# 標準入力で Claude Code から JSON を受け取り、Bash コマンドを検査する。
# 拒否する場合は終了コード 2 と標準エラーの理由を返す。
#
# パターン列挙では書き方の違い（refspec、リモート名、git -C、空白）で
# 迂回できるため、実際の Branch 名と push 先 ref を見て判定する。

set -uo pipefail

PROTECTED="${MIRUME_PROTECTED_BRANCH:-main}"

# stdin の JSON から .tool_input.command を取り出す。
# jq に依存しないよう python3 を使い、無い場合は素通しせず安全側で警告する。
read_command() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(d.get("tool_input", {}).get("command", ""))' 2>/dev/null
  fi
}

CMD="$(read_command)"
[ -n "$CMD" ] || exit 0

deny() {
  echo "MirumeAI 開発ルールにより拒否しました: $1" >&2
  echo "$2" >&2
  echo "Issue 番号付きの作業 Branch を作り、Pull Request 経由で変更してください。" >&2
  exit 2
}

# git 系と gh 系以外は対象外
case "$CMD" in
  *git*|*gh\ *) ;;
  *) exit 0 ;;
esac

# --- 1) 保護 Branch 上での commit ---
if printf '%s' "$CMD" | grep -qE '(^|[;&|]|&&|\|\|)[[:space:]]*git([[:space:]]+-[^[:space:]]+([[:space:]]+[^[:space:]]+)?)*[[:space:]]+commit([[:space:]]|$)'; then
  CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
  if [ "$CUR" = "$PROTECTED" ]; then
    deny "保護Branch '$PROTECTED' 上での commit" \
         "現在の Branch: $CUR"
  fi
fi

# --- 2) push ---
if printf '%s' "$CMD" | grep -qE '(^|[;&|]|&&|\|\|)[[:space:]]*git([[:space:]]+-[^[:space:]]+([[:space:]]+[^[:space:]]+)?)*[[:space:]]+push([[:space:]]|$)'; then

  # force push は refspec に関係なく拒否
  if printf '%s' "$CMD" | grep -qE '(--force([-=][a-z-]+)?|[[:space:]]-[a-zA-Z]*f([[:space:]]|$)|--mirror)'; then
    deny "force push" "検出した指定: $(printf '%s' "$CMD" | grep -oE '(--force[-=a-z]*|--mirror|[[:space:]]-[a-zA-Z]*f([[:space:]]|$))' | head -1 | tr -d ' ')"
  fi

  # 保護 Branch の削除
  if printf '%s' "$CMD" | grep -qE "(--delete|[[:space:]]-d)([[:space:]]+[^[:space:]]+)*[[:space:]]+${PROTECTED}([[:space:]]|$)" \
   || printf '%s' "$CMD" | grep -qE "[[:space:]]:(refs/heads/)?${PROTECTED}([[:space:]]|$)"; then
    deny "保護Branch '$PROTECTED' の削除" "検出したコマンド: $CMD"
  fi

  # 明示的な push 先が保護 Branch
  if printf '%s' "$CMD" | grep -qE "[[:space:]]\+?([^[:space:]:]+:)?(refs/heads/)?${PROTECTED}([[:space:]]|$)"; then
    deny "保護Branch '$PROTECTED' への push" "検出したコマンド: $CMD"
  fi

  # refspec 省略時は現在の Branch と upstream を見る
  if ! printf '%s' "$CMD" | grep -qE '[[:space:]](\+|[^-[:space:]][^[:space:]]*:)'; then
    CUR="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '')"
    UP="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || echo '')"
    if [ "$CUR" = "$PROTECTED" ] || [ "${UP##*/}" = "$PROTECTED" ]; then
      deny "保護Branch '$PROTECTED' への push" "現在の Branch: ${CUR:-不明} / upstream: ${UP:-未設定}"
    fi
  fi
fi

# --- 3) GitHub API による書き込み ---
if printf '%s' "$CMD" | grep -qE '(^|[;&|]|&&|\|\|)[[:space:]]*gh[[:space:]]+api([[:space:]]|$)' \
 && printf '%s' "$CMD" | grep -qE '(-X|--method)[[:space:]]+(PUT|POST|PATCH|DELETE)'; then
  deny "gh api による書き込み" "Repository の内容や設定を API で直接変更しないでください。"
fi

exit 0

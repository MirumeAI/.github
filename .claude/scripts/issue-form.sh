#!/usr/bin/env bash
# MirumeAI Issue Form ヘルパー
#
# Issue Form の正本 (MirumeAI/.github) からラベルを取得し、
# CLI で作成する Issue 本文が Form の出力形式と一致するようにする。
#
#   issue-form.sh labels   <feature|bug|experiment>
#   issue-form.sh skeleton <feature|bug|experiment>
#   issue-form.sh check    <feature|bug|experiment> <file>
#
# ラベルをこのファイルへ書き写さない。常に正本から取得する。

set -euo pipefail

FORM_REPO="MirumeAI/.github"
FORM_DIR=".github/ISSUE_TEMPLATE"

usage() {
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

_FORM_CACHE=""
_FORM_CACHE_TYPE=""

fetch_form() {
  local type="$1"
  case "$type" in
    feature|bug|experiment) ;;
    *) echo "種別は feature / bug / experiment のいずれかです: $type" >&2; exit 2 ;;
  esac
  if [ "$_FORM_CACHE_TYPE" = "$type" ]; then
    printf '%s\n' "$_FORM_CACHE"
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    echo "gh コマンドが必要です。01_getting_started/claude_code_setup.md を参照してください。" >&2
    exit 1
  fi
  _FORM_CACHE="$(gh api "repos/${FORM_REPO}/contents/${FORM_DIR}/${type}.yml" --jq .content | base64 -d)"
  _FORM_CACHE_TYPE="$type"
  printf '%s\n' "$_FORM_CACHE"
}

# 本文を正規化する。BOM を除去し、CRLF を LF にし、コードフェンス内の行を除外する。
# フェンス内を空行へ置き換えるため、報告する行番号は元ファイルと一致する。
normalize_body() {
  sed -e '1s/^\xEF\xBB\xBF//' -e 's/\r$//' "$1" \
    | awk '
        /^[[:space:]]*(```|~~~)/ { fence = !fence; print ""; next }
        { if (fence) print ""; else print }
      '
}

# ラベルを Form の定義順に出力する
labels() { fetch_form "$1" | sed -n 's/^      label: //p'; }

# 必須ラベルのみ出力する
required_labels() {
  fetch_form "$1" | awk '
    /^      label: /   { l = substr($0, 14) }
    /^      required: /{ if ($2 == "true") print l }'
}

# dropdown の選択肢を "ラベル<TAB>選択肢" で出力する
options() {
  fetch_form "$1" | awk '
    /^  - type: /      { intype = $3; inopt = 0 }
    /^      label: /   { l = substr($0, 14) }
    /^      options:/  { if (intype == "dropdown") inopt = 1; next }
    /^        - /      { if (inopt) { o = substr($0, 11); print l "\t" o } ; next }
    /^    validations:/{ inopt = 0 }'
}

title_prefix() { fetch_form "$1" | sed -n 's/^title: "\(.*\)"$/\1/p'; }

skeleton() {
  local type="$1" req opt
  req="$(required_labels "$type")"
  opt="$(options "$type")"

  echo "# --- ここから下が Issue 本文。この行と次の説明ブロックは本文に含めない ---" >&2
  echo "title の接頭辞: $(title_prefix "$type")" >&2
  echo "必須ラベル:" >&2
  echo "$req" | sed 's/^/  - /' >&2
  if [ -n "$opt" ]; then
    echo "選択肢（この中から1つだけ選ぶ。新しい値を作らない）:" >&2
    echo "$opt" | awk -F'\t' '{print "  " $1 ": " $2}' >&2
  fi
  echo "" >&2

  labels "$type" | while IFS= read -r l; do
    printf '### %s\n\n\n' "$l"
  done
}

check() {
  local type="$1" file="$2" expected actual missing extra body ng=0
  [ -f "$file" ] || { echo "ファイルがありません: $file" >&2; exit 2; }

  body="$(normalize_body "$file")"
  expected="$(labels "$type")"
  actual="$(printf '%s\n' "$body" | sed -n 's/^### //p')"

  # 1) 見出しの一致と順序
  if [ "$expected" != "$actual" ]; then
    ng=1
    echo "NG: 見出しが Form の定義と一致しません" >&2
    echo "--- 期待（正本の順序）---" >&2; echo "$expected" >&2
    echo "--- 実際 ---" >&2; echo "$actual" >&2
    missing="$(comm -23 <(echo "$expected" | sort) <(echo "$actual" | sort) || true)"
    extra="$(comm -13 <(echo "$expected" | sort) <(echo "$actual" | sort) || true)"
    [ -n "$missing" ] && { echo "--- 不足 ---" >&2; echo "$missing" >&2; }
    [ -n "$extra" ]   && { echo "--- 余分 ---" >&2; echo "$extra" >&2; }
  fi

  # 2) Form の出力は '### ' のみ。'# ' と '## ' を使わない（フェンス内は除外済み）
  if printf '%s\n' "$body" | grep -qE '^#{1,2} [^#]'; then
    ng=1
    echo "NG: 本文に '# ' または '## ' の見出しがあります。Form の出力は '### ' のみです" >&2
    printf '%s\n' "$body" | grep -nE '^#{1,2} [^#]' >&2
  fi

  # 3) 必須項目が空でないか
  local l empty=""
  while IFS= read -r l; do
    [ -n "$l" ] || continue
    if ! printf '%s\n' "$body" | awk -v lab="### $l" '
          $0 == lab { insec = 1; next }
          /^### /   { insec = 0 }
          insec && $0 !~ /^[[:space:]]*$/ && $0 !~ /^_No response_$/ { found = 1 }
          END { exit(found ? 0 : 1) }'; then
      empty="${empty}${l}\n"
    fi
  done <<< "$(required_labels "$type")"
  if [ -n "$empty" ]; then
    ng=1
    echo "NG: 必須項目が空です" >&2
    printf "$empty" | sed 's/^/  - /' >&2
  fi

  # 4) dropdown の値が選択肢に含まれるか
  local opts lab val
  opts="$(options "$type")"
  if [ -n "$opts" ]; then
    while IFS= read -r lab; do
      [ -n "$lab" ] || continue
      val="$(printf '%s\n' "$body" | awk -v l="### $lab" '
               $0 == l { insec = 1; next }
               /^### / { insec = 0 }
               insec && $0 !~ /^[[:space:]]*$/ { print; exit }')"
      if [ -n "$val" ] && ! printf '%s\n' "$opts" | grep -qxF "$(printf '%s\t%s' "$lab" "$val")"; then
        ng=1
        echo "NG: 「$lab」の値が選択肢にありません: $val" >&2
        echo "--- 選択肢 ---" >&2
        printf '%s\n' "$opts" | awk -F'\t' -v l="$lab" '$1 == l { print "  " $2 }' >&2
      fi
    done <<< "$(printf '%s\n' "$opts" | cut -f1 | sort -u)"
  fi

  # 5) 分量の目安（警告のみ。終了コードに反映しない）
  local lines
  lines="$(printf '%s\n' "$body" | wc -l)"
  if [ "$lines" -gt 60 ]; then
    echo "注意: 本文が ${lines} 行あります。目安は 60 行以内です。経緯や調査結果はコメントへ移してください。" >&2
  fi

  [ "$ng" -eq 0 ] || exit 1
  echo "OK: 見出し・必須項目・選択肢すべて Form の定義と一致しています"
}

[ $# -ge 1 ] || usage
cmd="$1"; shift
case "$cmd" in
  labels)   [ $# -eq 1 ] || usage; labels "$1" ;;
  skeleton) [ $# -eq 1 ] || usage; skeleton "$1" ;;
  check)    [ $# -eq 2 ] || usage; check "$1" "$2" ;;
  *) usage ;;
esac

#!/usr/bin/env python3
"""手元用の複製が、 正本とずれていないことを確かめる。

validator の正本は `MirumeAI/.github`。 CI は再利用 workflow 経由でそれを
実行するが、 各 Repository には手元実行用の複製（`scripts/ci/`）が残って
いる。 **複製である以上、 また drift する。**

実際に起きたこと（どれも人が気づけない形だった）:

  ・`basic_checks.py` の改善が 1 Repository にしか入らず、 残り 5 件は
    `.env` や拡張子の無いファイルに書かれた鍵を検出できなかった
  ・`import_check.py` の修正が 1 Repository にしか入らなかった
  ・`CLAUDE.md` の件数が 5 Repository で実測とずれていた

再利用 workflow は呼び出し側と正本の**両方を checkout している**ので、
照合はそこで行える。

    CANONICAL_ROOT=validators CI_ROOT=target python3 copy_sync_check.py

意図して違える場合は、 呼び出し側の `.ci-copy-allow` に理由つきで書く
（`.ci-ignore` / `.ci-imports-allow` と同じ考え方）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: 全 Repository で同じであるべきファイル。
#:
#: **`.claude/settings.json` は入れない。** Repository ごとの差分が
#: 意図的であることを実測で確認している（顧客 Repository は `.lic` を
#: deny に追加、 docs Repository は持たない script の許可を除外）。
SHARED = (
    "scripts/ci/basic_checks.py",
    "scripts/ci/import_check.py",
    "scripts/ci/test_guard_main.sh",
    # Plan 制約で branch protection が使えない Repository では、
    # これが main を守る唯一の機械的な壁になる。 **ずれてよいものではない。**
    ".claude/hooks/guard-main.sh",
    # 対象外にするファイルを `.ci-leak-allow` へ出したので、 script 本体は
    # 全 Repository で同一になった。 **中に表を持たせていた間は照合できず、
    # ここだけ drift が野放しだった。**
    "scripts/ci/customer_data_check.py",
    # Issue を作る手順。 種別の対応（#38）では、 正本を直した後に 5 件へ
    # 1 件ずつ同期した。 照合しないと、 同期を忘れた Repository だけ
    # 種別の無い Issue に戻る。
    ".claude/scripts/issue-form.sh",
    ".claude/commands/issue.md",
)

ALLOW_FILE = ".ci-copy-allow"


def _allowed(root: Path) -> set:
    """意図して違えるもの。 **理由を書かせる**（`#` 以降が理由）。"""
    f = root / ALLOW_FILE
    if not f.is_file():
        return set()
    out = set()
    for ln in f.read_text(encoding="utf-8").splitlines():
        path = ln.split("#")[0].strip()
        if path:
            out.add(path)
    return out


def main() -> int:
    target = Path(os.environ.get("CI_ROOT", ".")).resolve()
    canonical = Path(os.environ.get("CANONICAL_ROOT", "")).resolve() \
        if os.environ.get("CANONICAL_ROOT") else None
    if canonical is None or not canonical.is_dir():
        print("CANONICAL_ROOT が指定されていません。"
              " 正本（MirumeAI/.github）の場所を渡してください。", file=sys.stderr)
        return 2
    if target == canonical:
        # 正本そのもの。 比べる相手がいない。
        print("正本そのものなので照合しません。")
        return 0

    allow = _allowed(target)
    checked, differ, missing = 0, [], []
    for rel in SHARED:
        if rel in allow:
            continue
        t, c = target / rel, canonical / rel
        if not t.is_file():
            continue                     # 持っていない Repository では何もしない
        if not c.is_file():
            missing.append(rel)
            continue
        checked += 1
        if t.read_bytes() != c.read_bytes():
            differ.append(rel)

    print(f"照合: {checked} ファイル"
          + (f" / 対象外 {len(allow)}" if allow else ""))
    if missing:
        print(f"\n正本に無いファイルがあります: {', '.join(missing)}",
              file=sys.stderr)
        return 1
    if differ:
        print(f"\nNG: 正本とずれている複製 {len(differ)} 件", file=sys.stderr)
        for rel in differ:
            print(f"  {rel}", file=sys.stderr)
        print("\n  正本から取り直してください:", file=sys.stderr)
        print("    gh api repos/MirumeAI/.github/contents/<path>"
              " --jq .content | base64 -d > <path>", file=sys.stderr)
        print(f"  意図して違える場合は、 理由を書いて {ALLOW_FILE} へ"
              " 追加してください。", file=sys.stderr)
        return 1
    print("OK: 複製は正本と同じです")
    return 0


if __name__ == "__main__":
    sys.exit(main())

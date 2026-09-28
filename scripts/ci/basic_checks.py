#!/usr/bin/env python3
"""Pull Request の機械チェック。

**人間が読む前に、機械で分かることは機械が見る。** ここで見るのは
「壊れているかどうか」だけで、良し悪しの判断はしない。

見るもの:
  1. Python の構文
  2. JSON の妥当性（0バイトを含む）
  3. YAML の妥当性
  4. 大きすぎるファイル
  5. 秘密情報らしき文字列
  6. シェルスクリプトの構文

除外は `.ci-ignore`（1行1パターン、glob）で指定する。
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024

#: 追跡対象から常に外す。生成物・仮想環境・ベンダーコード。
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             "PDM", "PDM_versions", "weights", "data", "datasets", "results"}

#: 空でよいファイル。置き場所を作るためのもの。
ALLOW_EMPTY = {".gitkeep", ".gitignore", ".keep", "__init__.py", "py.typed"}

#: VERSION ファイルの形。 リリースのタグと文字列として突き合わせるので、
#: 前後に何も付けない。
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

#: 秘密情報らしき形。**値を出力しない**（ログに残すと二次漏洩になる）。
SECRET_PATTERNS = [
    ("AWS アクセスキー", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub トークン", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("GitHub PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("OpenAI キー", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("秘密鍵", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("Slack トークン", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
]

TEXT_SUFFIXES = {".py", ".sh", ".md", ".json", ".yml", ".yaml", ".txt", ".cfg",
                 ".toml", ".ini", ".html", ".js", ".css", ".service", ".desktop",
                 # 鍵や設定がそのまま入りやすい形。 **ここを外すと素通りする。**
                 ".env", ".pem", ".key", ".crt", ".conf", ".properties", ".sql"}

#: 拡張子の無いテキストファイルも秘密情報の走査にかける。
#:
#: **拡張子で絞ると、 拡張子の無いファイルが丸ごと素通りする。**
#: 実際に `configs/active_profile` のような拡張子なしファイルがあり、
#: そこへ鍵を書いても検出できなかった（監査で実証）。
#: 中身が読めてテキストなら見る、 という判断にする。
MAX_SECRET_SCAN_BYTES = 2 * 1024 * 1024


def looks_like_text(path: Path, size: int) -> bool:
    """秘密情報の走査にかけてよいか（拡張子なしファイル用）。"""
    if size == 0 or size > MAX_SECRET_SCAN_BYTES:
        return False
    try:
        head = path.open("rb").read(4096)
    except OSError:
        return False
    if b"\x00" in head:            # NUL があればバイナリ
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def load_ignores(root: Path) -> list[str]:
    f = root / ".ci-ignore"
    if not f.is_file():
        return []
    return [ln.strip() for ln in f.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def _git_files(root: Path, *extra: str) -> list[str]:
    """追跡しているファイル + **まだ追跡していない新しいファイル**。

    `git ls-files` だけを見ていたので、 **新しく作ったファイルは
    `git add` するまで検査されなかった**。 手元で「OK」と出たものが、
    commit した後の CI で初めて落ちる。 今日 3 回起きた。

    `--others --exclude-standard` で、 `.gitignore` に入っているもの
    （生成物・秘密の置き場）は除いたまま、 新しいファイルを足す。
    """
    seen, out = set(), []
    for args in (["git", "ls-files", "-z", *extra],
                 ["git", "ls-files", "-z", "--others", "--exclude-standard",
                  *extra]):
        got = subprocess.run(args, cwd=root, capture_output=True, text=True,
                             check=True).stdout
        for rel in got.split("\0"):
            if rel and rel not in seen:
                seen.add(rel)
                out.append(rel)
    return out


def tracked_files(root: Path, ignores: list[str]) -> list[Path]:
    files = []
    for rel in _git_files(root):
        p = Path(rel)
        if SKIP_DIRS & set(p.parts):
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in ignores):
            continue
        if (root / p).is_file():
            files.append(p)
    return files


def main() -> int:
    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    ignores = load_ignores(root)
    files = tracked_files(root, ignores)
    errors: list[str] = []
    counts = {"py": 0, "json": 0, "yaml": 0, "sh": 0}

    try:
        import yaml
    except ImportError:
        yaml = None          # 無ければ YAML の検査だけ省く

    for rel in files:
        p = root / rel
        size = p.stat().st_size

        # 4) 大きすぎるファイル
        if size > MAX_BYTES:
            errors.append(f"{rel}: {size / 1048576:.1f} MB > {MAX_BYTES // 1048576} MB。"
                          f"大容量ファイルは Repository の外で管理する")

        # 空ファイル（置き場所用を除く）
        if size == 0 and p.name not in ALLOW_EMPTY:
            errors.append(f"{rel}: 0 バイト。意図した空ファイルなら "
                          f"`.gitkeep` にするか `.ci-ignore` へ追加する")
            continue

        suffix = p.suffix.lower()

        # 1) Python の構文
        if suffix == ".py":
            counts["py"] += 1
            try:
                ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(rel))
            except SyntaxError as e:
                errors.append(f"{rel}:{e.lineno}: 構文エラー: {e.msg}")

        # 2) JSON
        elif suffix == ".json":
            counts["json"] += 1
            try:
                json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception as e:
                errors.append(f"{rel}: JSON として読めない: {e}")

        # 3) YAML
        elif suffix in (".yml", ".yaml"):
            counts["yaml"] += 1
            if yaml is not None:
                try:
                    list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
                except Exception as e:
                    errors.append(f"{rel}: YAML として読めない: {e}")

        # 6) シェルスクリプト
        elif suffix == ".sh":
            counts["sh"] += 1
            r = subprocess.run(["bash", "-n", str(p)], capture_output=True, text=True)
            if r.returncode != 0:
                errors.append(f"{rel}: シェルの構文エラー: {r.stderr.strip().splitlines()[0] if r.stderr.strip() else ''}")

        # 5) 秘密情報（テキストのみ。**一致した値は出さない**）
        scan = (suffix in TEXT_SUFFIXES and size <= MAX_SECRET_SCAN_BYTES) or (
            suffix == "" and looks_like_text(p, size))
        if scan:
            body = p.read_text(encoding="utf-8", errors="replace")
            for label, pat in SECRET_PATTERNS:
                m = pat.search(body)
                if m:
                    line = body[:m.start()].count("\n") + 1
                    errors.append(f"{rel}:{line}: {label} らしき文字列。"
                                  f"誤検出なら `.ci-ignore` へ追加し、"
                                  f"本物なら**必ず無効化・再発行**する")
                    break

    # 6) VERSION ファイル（あれば）の形式。
    #    **タグと突き合わせる唯一の出どころなので、 形が崩れると
    #    リリースが作れない。** 無い Repository では何もしない。
    vfile = root / "VERSION"
    if vfile.is_file():
        raw = vfile.read_text(encoding="utf-8", errors="replace")
        v = raw.strip()
        if not VERSION_RE.fullmatch(v):
            errors.append("VERSION: 'x.y.z' の形にしてください"
                          f"（いまは {v[:40]!r}）")
        elif raw != v + "\n":
            errors.append("VERSION: 版だけを1行で書いてください"
                          "（前後の空白や複数行は不可）")

    print(f"検査: {len(files)} ファイル "
          f"(py={counts['py']} json={counts['json']} yaml={counts['yaml']} sh={counts['sh']})")
    if yaml is None:
        print("注意: PyYAML が無いため YAML の検査を省略しました")

    if errors:
        print(f"\nNG: {len(errors)} 件")
        for e in errors:
            print(f"  {e}")
        return 1
    print("OK: 問題なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())

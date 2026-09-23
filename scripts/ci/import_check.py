#!/usr/bin/env python3
"""import しているのに宣言していない依存を見つける。

**「手元では動くのに、新しい環境で ModuleNotFoundError」を merge 前に止める。**
実際に `cryptography` の記載漏れで `import mirumeai_auth` が失敗する状態が
main に入った。これはこの検査で防げる。

実際にインストールはしない（torch や CUDA を入れると CI が重すぎる）。
`import` 文を読み、 次のどれにも当てはまらないものを報告する。

  1. 標準ライブラリ
  2. この Repository 内のモジュール
  3. requirements.txt / pyproject.toml で宣言済み
  4. `.ci-imports-allow` に列挙（ベンダー SDK、 別 venv から来るもの等）

`try: import X / except ImportError` で囲まれたものは、 無くても動く前提と
みなして対象外にする。
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules",
             "PDM", "PDM_versions", "weights", "data", "results", "backbones"}

#: import 名 → 配布パッケージ名。 名前が一致しないものだけ書く。
DIST_ALIAS = {
    "cv2": ("opencv-python", "opencv-python-headless", "opencv-contrib-python"),
    "PIL": ("pillow",),
    "yaml": ("pyyaml",),
    "sklearn": ("scikit-learn",),
    "skimage": ("scikit-image",),
    "serial": ("pyserial",),
    "dateutil": ("python-dateutil",),
    "dotenv": ("python-dotenv",),
    "OpenGL": ("pyopengl",),
    "fitz": ("pymupdf",),
}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def declared(root: Path) -> set[str]:
    out: set[str] = set()
    for rel in ("requirements.txt", "requirements-dev.txt"):
        f = root / rel
        if f.is_file():
            for ln in f.read_text(encoding="utf-8").splitlines():
                ln = ln.split("#")[0].strip()
                if not ln or ln.startswith("-"):
                    continue
                out.add(norm(re.split(r"[<>=!\[;\s]", ln)[0]))
    f = root / "pyproject.toml"
    if f.is_file():
        body = f.read_text(encoding="utf-8")
        for m in re.finditer(r'"([A-Za-z0-9._\-]+)\s*(?:[<>=!\[][^"]*)?"', body):
            out.add(norm(m.group(1)))
    return out


def local_modules(root: Path) -> set[str]:
    out = set()
    for p in root.iterdir():
        if p.name.startswith(".") or p.name in SKIP_DIRS:
            continue
        if p.is_dir() and (p / "__init__.py").exists():
            out.add(p.name)
        elif p.suffix == ".py":
            out.add(p.stem)
    src = root / "src"
    if src.is_dir():
        for p in src.iterdir():
            if p.is_dir() and (p / "__init__.py").exists():
                out.add(p.name)
    return out


def guarded_lines(tree: ast.AST) -> set[int]:
    """try/except ImportError で囲まれた import の行番号。"""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            handled = any(
                (isinstance(h.type, ast.Name) and h.type.id in ("ImportError", "Exception", "ModuleNotFoundError"))
                or (isinstance(h.type, ast.Tuple)
                    and any(isinstance(e, ast.Name) and e.id in ("ImportError", "Exception", "ModuleNotFoundError")
                            for e in h.type.elts))
                or h.type is None
                for h in node.handlers)
            if handled:
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        out.add(sub.lineno)
    return out


def main() -> int:
    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    allow_f = root / ".ci-imports-allow"
    allow = set()
    if allow_f.is_file():
        allow = {norm(ln.split("#")[0]) for ln in allow_f.read_text(encoding="utf-8").splitlines()
                 if ln.split("#")[0].strip()}

    decl = declared(root)
    local = local_modules(root)
    std = set(sys.stdlib_module_names)

    files = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=root,
                           capture_output=True, text=True, check=True).stdout
    missing: dict[str, list[str]] = {}
    n = 0
    for rel in files.split("\0"):
        if not rel or SKIP_DIRS & set(Path(rel).parts):
            continue
        p = root / rel
        if not p.is_file():
            continue
        n += 1
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue          # 構文は basic_checks.py が見る
        skip = guarded_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:                 # 相対 import は自分の中
                    continue
                names = [(node.module or "").split(".")[0]]
            else:
                continue
            if node.lineno in skip:
                continue
            for top in names:
                if not top or top in std or top in local or norm(top) in allow:
                    continue
                cands = {norm(top), *(norm(x) for x in DIST_ALIAS.get(top, ()))}
                if cands & decl:
                    continue
                missing.setdefault(top, []).append(f"{rel}:{node.lineno}")

    print(f"検査: {n} ファイル / 宣言済み依存 {len(decl)} / 許可リスト {len(allow)}")
    if missing:
        print(f"\nNG: 宣言されていない依存 {len(missing)} 件")
        for mod, where in sorted(missing.items()):
            print(f"  {mod}: {where[0]}" + (f" 他 {len(where)-1} 箇所" if len(where) > 1 else ""))
        print("\n  requirements.txt か pyproject.toml へ追加するか、")
        print("  外部 SDK・別 venv 由来なら .ci-imports-allow へ追加してください。")
        return 1
    print("OK: 宣言されていない依存はありません")
    return 0


if __name__ == "__main__":
    sys.exit(main())

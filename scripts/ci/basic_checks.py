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

**CI の記録に、 入力の中身と顧客に固有の道を出さない**（#50）。
顧客案件の Repository では、 設定のファイルの道や中身に、 その案件に
固有の名前（面・トリガ・機器）や値が入る。 誤りを見つけたときに
PyYAML の誤りの文（該当行の抜き出しを含む）や `bash -n` の出力、
ファイルの道をそのまま出していたので、 それが CI の記録に残った。

  ・誤りの文は、 どの Repository でも種類と行（と列）だけにする。
    入力の行・パーサの文脈・`VERSION` の中身・例外の文は出さない
  ・道は、 顧客の値を持たない共通の Repository（`OPEN_PATH_REPOS`）の
    ときだけそのまま出す。 それ以外と、 どの Repository か分からない
    ときは、 決まった名前（`GENERIC_PARTS`）の外を `<redacted#n>` にする
  ・出す行は、 制御文字と Actions の命令として読まれる並び（`::`・`##[`）を
    無害にしてから出す。 想定外の例外でも traceback は出さない（終了コード 2）

手元で、 伏せた道の元を知りたいときだけ `--show-paths` を付ける
（CI では使えない）。
"""
from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import subprocess
import sys
import unicodedata
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

#: 道をそのまま出してよい Repository（顧客の値を持たない共通のもの）。
#: **ここに無い Repository と、 どの Repository か分からないときは伏せる。**
#: 顧客案件の Repository（`customer-<会社>-<部品>`）を足してはいけない。
OPEN_PATH_REPOS = {
    "mirumeai/.github",
    "mirumeai/development-docs",
    "mirumeai/pdm",
    "mirumeai/image-acquisition-pdm",
}

#: 伏せるときにも、 そのまま出してよい道の部分。 どの案件にも共通の置き場と
#: 決まった名前だけを書く。 **案件に固有の名前（面・トリガ・機器・資料の名前）は
#: 書かない。** 書いていない部分は `<redacted#n>` になる。
GENERIC_PARTS = frozenset({
    # Repository の置き場
    ".github", "workflows", "ISSUE_TEMPLATE", ".claude", "hooks", "commands",
    "scripts", "ci", "tests", "config", "configs", "image-acquisition", "pdm",
    "deploy", "deployment", "release", "docs", "plugins", "templates",
    "acceptance", "evaluation", "manifests", "poc", "quick-diagnosis",
    "recipes", "requirements", "validation", "profiles", "supervised",
    "defaults",
    # Core が決めている面ごとの置き場（customer_isolation_check.py と同じ）
    "adaptive_mask", "align", "bbox_acquire", "good_pool", "region_split",
    # 決まった名前のファイル
    "README.md", "CLAUDE.md", "VERSION", "versions.yaml", "customer-profile.yaml",
    "requirements.txt", "pyproject.toml", "config.json", "pdm_guard_policy.json",
    "recipe.json", "machine.json", "align_reference.txt", "settings.json",
    "basic-checks.yml", ".gitignore", ".gitkeep", ".env", ".ci-ignore",
    ".ci-imports-allow", ".ci-copy-allow", ".ci-leak-allow",
    # 全 Repository で同じ複製（copy_sync_check.SHARED の名前）
    "basic_checks.py", "import_check.py", "test_guard_main.sh", "guard-main.sh",
    "customer_data_check.py", "issue-form.sh", "issue.md", "branch.md", "pr.md",
    "docs-sync.md", "customer-request.md",
})

#: 伏せた名前に残してよい拡張子の形。
_SAFE_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,10}")

#: `origin` の URL から Repository を取る（https と ssh の両方）。
_GITHUB_REPO = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/*$")


def neutral(text: str) -> str:
    """出す行を無害にする。

    制御文字（改行を含む）と書式の文字を `\\uXXXX` にし、 Actions の命令として
    読まれる並び（行頭の `::`、 行のどこにあっても読まれる `##[`）を崩す。
    **ファイルの名前は誰でも付けられる**ので、 名前に改行と `::warning::` を
    入れると、 記録の上で命令として読まれていた。
    """
    out = []
    for ch in text:
        if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp"):
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out).replace("::", ":\\u003a").replace("##[", "#\\u0023[")


def origin_repo(root: Path) -> "str | None":
    """`owner/name`（小文字）。 分からなければ None。"""
    try:
        url = subprocess.run(["git", "config", "--get", "remote.origin.url"],
                             cwd=root, capture_output=True, text=True,
                             errors="replace").stdout.strip()
    except OSError:
        return None
    m = _GITHUB_REPO.search(url)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}".lower()


def opens_paths(root: Path) -> bool:
    """道をそのまま出してよいか。 **分からなければ出さない。**"""
    if (root / "customer-profile.yaml").is_file():
        return False
    return origin_repo(root) in OPEN_PATH_REPOS


class FileIds:
    """記録に出すファイルの識別子。

    伏せるときは、 `GENERIC_PARTS` の外の部分を `<redacted#n>` にする。
    同じ名前には同じ番号を振るので、 同じ置き場の誤りだと分かる（元には戻せない）。
    """

    def __init__(self, open_paths: bool) -> None:
        self.open_paths = open_paths
        self._seen: dict = {}

    def __call__(self, rel: Path) -> str:
        if self.open_paths:
            return neutral(rel.as_posix())
        parts = []
        for i, part in enumerate(rel.parts):
            if part in GENERIC_PARTS:
                parts.append(part)
                continue
            n = self._seen.setdefault(part, len(self._seen) + 1)
            suffix = Path(part).suffix if i == len(rel.parts) - 1 else ""
            if not _SAFE_SUFFIX.fullmatch(suffix):
                suffix = ""
            parts.append(f"<redacted#{n}>{suffix}")
        return "/".join(parts)


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


def _yaml_where(e: Exception) -> tuple:
    """PyYAML の誤りの位置（行・列）。 **誤りの文と抜き出しは使わない。**"""
    mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
    if mark is None:
        return "", ""
    return f":{mark.line + 1}", f"・列 {mark.column + 1}"


def check_file(root: Path, rel: Path, rid: str, yaml, counts: dict,
               errors: list) -> None:
    """1 ファイルを見る。 誤りの文は種類と位置だけ（入力の中身を入れない）。"""
    p = root / rel
    size = p.stat().st_size

    # 4) 大きすぎるファイル
    if size > MAX_BYTES:
        errors.append(f"{rid}: {size / 1048576:.1f} MB > {MAX_BYTES // 1048576} MB。"
                      f"大容量ファイルは Repository の外で管理する")

    # 空ファイル（置き場所用を除く）
    if size == 0 and p.name not in ALLOW_EMPTY:
        errors.append(f"{rid}: 0 バイト。意図した空ファイルなら "
                      f"`.gitkeep` にするか `.ci-ignore` へ追加する")
        return

    suffix = p.suffix.lower()

    # 1) Python の構文（誤りの文は、 元の行の一部を含むことがあるので出さない）
    if suffix == ".py":
        counts["py"] += 1
        try:
            ast.parse(p.read_text(encoding="utf-8", errors="replace"),
                      filename="<checked>")
        except SyntaxError as e:
            col = f"・列 {e.offset}" if e.offset else ""
            errors.append(f"{rid}:{e.lineno or '?'}: 構文エラー（{type(e).__name__}{col}）")
        except ValueError as e:          # NUL を含む（3.10 は SyntaxError でない）
            errors.append(f"{rid}: 構文エラー（{type(e).__name__}。NUL などを含む）")

    # 2) JSON（位置だけ。 誤りの文には入力の文字が入ることがある）
    elif suffix == ".json":
        counts["json"] += 1
        try:
            json.loads(p.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as e:
            errors.append(f"{rid}:{e.lineno}: JSON として読めない（列 {e.colno}）")
        except UnicodeDecodeError:
            errors.append(f"{rid}: JSON として読めない（UTF-8 でない）")
        except Exception as e:
            errors.append(f"{rid}: JSON として読めない（{type(e).__name__}）")

    # 3) YAML（**PyYAML の誤りの文は、 該当行の抜き出しを含む**）
    elif suffix in (".yml", ".yaml"):
        counts["yaml"] += 1
        if yaml is not None:
            try:
                list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
            except Exception as e:
                line, col = _yaml_where(e)
                errors.append(f"{rid}{line}: YAML として読めない（{type(e).__name__}{col}）")

    # 6) シェルスクリプト（`bash -n` の出力は道と字句を含むので、 行の番号だけ取る）
    elif suffix == ".sh":
        counts["sh"] += 1
        r = subprocess.run(["bash", "-n", str(p)], capture_output=True,
                           text=True, errors="replace")
        if r.returncode != 0:
            m = re.search(r": line (\d+): ", r.stderr)
            errors.append(f"{rid}{':' + m.group(1) if m else ''}: シェルの構文エラー")

    # 5) 秘密情報（テキストのみ。**一致した値は出さない**）
    scan = (suffix in TEXT_SUFFIXES and size <= MAX_SECRET_SCAN_BYTES) or (
        suffix == "" and looks_like_text(p, size))
    if scan:
        body = p.read_text(encoding="utf-8", errors="replace")
        for label, pat in SECRET_PATTERNS:
            m = pat.search(body)
            if m:
                line = body[:m.start()].count("\n") + 1
                errors.append(f"{rid}:{line}: {label} らしき文字列。"
                              f"誤検出なら `.ci-ignore` へ追加し、"
                              f"本物なら**必ず無効化・再発行**する")
                break


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    show_paths = "--show-paths" in args
    if show_paths and (os.environ.get("GITHUB_ACTIONS") == "true"
                       or os.environ.get("CI", "").lower() in ("1", "true")):
        print("ERROR: --show-paths は CI では使えません（CI の記録に道が残るため）")
        return 2

    root = Path(os.environ.get("CI_ROOT", ".")).resolve()
    ignores = load_ignores(root)
    files = tracked_files(root, ignores)
    open_paths = show_paths or opens_paths(root)
    rid_of = FileIds(open_paths)
    errors: list[str] = []
    counts = {"py": 0, "json": 0, "yaml": 0, "sh": 0}

    try:
        import yaml
    except ImportError:
        yaml = None          # 無ければ YAML の検査だけ省く

    for rel in files:
        rid = rid_of(rel)
        try:
            check_file(root, rel, rid, yaml, counts, errors)
        except Exception as e:
            # **例外の文は道や中身を含みうる**ので、 種類だけを出す
            errors.append(f"{rid}: 検査できない（{type(e).__name__}）")

    # 6) VERSION ファイル（あれば）の形式。
    #    **タグと突き合わせる唯一の出どころなので、 形が崩れると
    #    リリースが作れない。** 無い Repository では何もしない。
    #    中身は出さない（長さだけ）。
    vfile = root / "VERSION"
    if vfile.is_file():
        raw = vfile.read_text(encoding="utf-8", errors="replace")
        v = raw.strip()
        if not VERSION_RE.fullmatch(v):
            errors.append("VERSION: 'x.y.z' の形にしてください"
                          f"（いまは {len(v)} 文字）")
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
            print(f"  {neutral(e)}")
        if not open_paths:
            print("\n  道は、決まった名前の外を伏せています（<redacted#n>）。"
                  "手元で --show-paths を付けると表示します（CI では使えません）。")
        return 1
    print("OK: 問題なし")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        # **traceback は道と中身を含みうる。** 種類だけを出し、 合格にはしない。
        print(f"ERROR: 検査の途中で想定外の例外（{type(e).__name__}）。"
              "道と中身は出しません")
        sys.exit(2)

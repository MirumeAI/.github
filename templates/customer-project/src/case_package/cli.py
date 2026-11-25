"""命令 `<案件の名前>`。

    <案件の名前> doctor [--judge-venv]
            この環境の確かめ（読むだけ）。 製品のパッケージ・Python・PyYAML の版と、 入っている Core の版が組める
            範囲（`cores.py`）に入るかを出す。 撮像側は要る。 PDM は `--judge-venv`（判定の venv として確かめる）の
            とき要り、 付けないときは入っていれば照らす
    <案件の名前> --version

どの命令も Core のコードを読み込まない（Core の版は dist-info の記録を文字として読む）。
"""
from __future__ import annotations

import argparse
import platform
import sys
import sysconfig
from pathlib import Path

from case_package import __version__, cores

#: 命令の名前（`pyproject.toml` の `[project.scripts]` と同じ）
PROG = "<案件の名前>"
#: 動く Python の下限（`pyproject.toml` の `requires-python` と同じ）
MIN_PYTHON = (3, 10)
#: PyYAML の系列（`pyproject.toml` の `dependencies` と同じ）
YAML_MAJOR = "6"


def _yaml_version() -> "str | None":
    try:
        import yaml
    except ImportError:
        return None
    return str(getattr(yaml, "__version__", "") or "") or None


def doctor(judge_venv: bool = False, site_packages: "Path | None" = None) -> int:
    """この環境の確かめ。 読むだけ。 動かせるとき 0、 動かせないとき 1。 `site_packages` は試験のためだけに渡す。"""
    print(f"{PROG} {__version__}")
    print(f"Python {platform.python_version()}")
    if sys.version_info[:2] < MIN_PYTHON:
        print(f"NG: Python {'.'.join(map(str, MIN_PYTHON))} 以上が要ります", file=sys.stderr)
        return 1
    ver = _yaml_version()
    if ver is None or ver.split(".")[0] != YAML_MAJOR:
        print(f"NG: PyYAML の {YAML_MAJOR} の系列が要ります（いまは {ver or '無い'}）", file=sys.stderr)
        return 1
    print(f"PyYAML {ver}")
    site = Path(site_packages) if site_packages is not None else Path(sysconfig.get_paths()["purelib"])
    dists = [cores.DIST_IA]
    if judge_venv:
        dists.append(cores.DIST_PDM)
    else:
        try:
            cores.installed_version(site, cores.DIST_PDM)
            dists.append(cores.DIST_PDM)
        except cores.CoreMissing:
            print(f"{cores.DIST_PDM}: この環境には入っていません（判定の venv は --judge-venv で確かめる）")
        except cores.CoreVersionError:
            dists.append(cores.DIST_PDM)            # 2 つ以上・読めないときも照らして止める
    ok, bad = cores.check(site, tuple(dists))
    for line in ok:
        print(line)
    for line in bad:
        print(f"NG: {line}", file=sys.stderr)
    if bad:
        return 1
    print("OK")
    return 0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=PROG, description="この案件の製品のパッケージの命令")
    p.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    sub = p.add_subparsers(dest="command", metavar="<命令>")
    d = sub.add_parser("doctor", help="この環境の確かめ（読むだけ）")
    d.add_argument("--judge-venv", action="store_true", help="判定の venv として確かめる（PDM も要る）")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = _parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "doctor":
        return doctor(judge_venv=args.judge_venv)
    _parser().print_usage(sys.stderr)
    return 2

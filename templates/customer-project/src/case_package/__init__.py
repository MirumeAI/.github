"""この案件の製品のパッケージ。

命令は `doctor` だけ（`cli.py`）。 組める Core の版の範囲は `cores.py`。 形の正本は MirumeAI/development-docs の
`03_customer_development/customer_repository_structure.md` と `recipes_and_machines.md` の「製品のパッケージの入れ方」。
**Core の内部は import しない**（使うのは各 Core の公開の面と差し込みの群だけ。 `tests/product/test_no_core_import.py`）。
"""

__version__ = "0.1.0"

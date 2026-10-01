# CLAUDE.md

MirumeAI 標準開発ルールに従う。
ルールの正本: `MirumeAI/development-docs` の `CLAUDE.md` および各ドキュメント。

## 絶対に守ること

1. `main` へ直接 commit / push しない。force push しない。
2. 原則 Issue から始める。Branch 名に Issue 番号を含める。
3. 変更は Pull Request 経由で `main` に入れる。Merge は Squash and merge。
4. **Core の Source をこの Repository へコピーしない。** `PDM/` と
   `Image-Acquisition-PDM/` は作業用 checkout で、`.gitignore` 済み。
5. 顧客データ、Credential、大容量 Dataset、Model Weight を置かない。
6. ライセンスファイル（`*.lic`）を置かない。

1 は `.claude/hooks/guard-main.sh` が機械的に拒否する。`cd` と `git -C` の対象
Repository を解決してから Branch を判定し、 先頭の飾り（`env`、`bash -c`、
絶対パス等）も剥がすため、 書き方を変えた程度では抜けない。
`gh` の書き込み系サブコマンドと、 ガード自身の書き換えも拒否する。

**ただしこれは「うっかり」を止める壁であって、 敵対的な回避を完全に塞ぐものではない。**
ターミナルから直接実行した場合は防げない。 GitHub Free かつ Private Repository では
Branch protection を設定できないため、 これと運用ルールが唯一の防護である。
挙動は `scripts/ci/test_guard_main.sh` が 65 ケースで検証している。

## この Repository の位置づけ

<案件の名前> 向けの**顧客固有差分のみ**を持つ Repository です。

```text
PDM Core + Image-Acquisition-PDM Core + この Repository = <案件の名前> の検査システム
```

使用する Core の組合せは `versions.yaml` で固定します。

## 変更を加えるとき

顧客要求は実装前に二段階で分類する。

- 対象Component: `PDM` / `Image-Acquisition-PDM` / `Both` / `Customer only`
- 実装分類: `Config` / `Core` / `Core + Config` / `Plugin・Adapter` / `Architecture Review`

**設定だけで対応できるならこの Repository、一般製品機能として成立するなら Core** へ実装する。
顧客名を除いても成立する機能をこの Repository に閉じ込めない。判断できない場合は
Architecture Review としてチームに相談する。

Core を変更した場合は、Core 側で Release したうえで `versions.yaml` を
検証済みの組合せへ更新する。

# poc/ — 実現性の検証

技術的に成立するかを確かめた記録を置きます（案件の段の PoC。根の `README.md` の「構造」）。

- 測ったデータの版（manifest の sha256・ファイルの数・大きさ。作り方は `MirumeAI/development-docs` の `03_customer_development/data_and_models.md` の「manifest と hash」）。評価に使うデータは**あとから変えない**（変えると前の結果と比べられない。変えたら版を上げ、どちらの版で測ったかを書く）
- 試した方式と結果
- 主要なリスクが解消されたかどうか

## 置かないもの

- **本番のコード。** PoC で必要性が確認できた共通機能は、Core の Issue → Branch → Required Verification → Pull Request → Review → Release で正式化します
- Core の `main` へ PoC のコードを直接ためること
- 画像・Dataset・モデルの本体と、データの manifest そのもの（データの置き場に置く。ここには ID・ファイルの数・大きさ・manifest の sha256 だけを書く）

**PoC は本番ではありません。** ここで動いたことは、本番で動くことを意味しません。

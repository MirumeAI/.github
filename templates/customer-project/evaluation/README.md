# evaluation/ — 評価の結果と、値を選んだ記録

**同じデータで測った結果**を残します。

- 測ったデータの版（manifest の sha256・ファイルの数・大きさ。評価用の Dataset の ID〔`DS-<案件の記号>-eval-v<番号>`〕を付けたら ID も）。作り方は `MirumeAI/development-docs` の `03_customer_development/data_and_models.md` の「manifest と hash」
- 未検出・過検出の件数
- 面ごとの内訳
- この案件で値を選んだ記録（選んだ値・根拠の数・測ったデータの版〔manifest の sha256〕）。判定側の設定とそろえた形と置き場は `MirumeAI/development-docs#273` で決めます。決まるまでは、ここが仮の置き場です

`evaluation/` は顧客の間の混入の検査が照らしません。ほかの案件の値・IP・製造番号を置きません。秘密・資格情報・顧客データは、どの案件のものも置きません（Private でも同じ）。混入の検査が照らすかは `MirumeAI/development-docs#273` で決めます。それまでは、記録の Pull Request で、ほかの案件の値・秘密・資格情報が入っていないかを Reviewer B が見ます。

## 置かないもの

- 毎回変わるデータでの結果（比較にならない）
- 画像・Dataset・モデルの本体と、データの manifest そのもの（データの置き場に置く。ここには ID・ファイルの数・大きさ・manifest の sha256 だけを書く）
- データの置き場の実際の道

**評価のデータを変えたら、それまでの数字と比べられません。** 変える場合は版を上げ（manifest の sha256 も変わる）、どちらの版で測ったかを必ず書きます。

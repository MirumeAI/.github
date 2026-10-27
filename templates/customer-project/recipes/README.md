# recipes/ — 製品・検査条件

製品ごとの検査条件、閾値、検査範囲を置きます。

## 撮像の条件（`recipes/capture/`）

撮像の条件の記録は `recipes/capture/` に置きます。カメラごとの物理の条件の表（`README.md`）と、製品ごとの条件の写し（`<製品>.json`）です。形・決め方・照らし方の正本は `MirumeAI/Image-Acquisition-PDM` の `docs/capture_conditions.md` です。

`recipes/` は顧客の間の混入の検査が照らしません。ほかの案件の値・IP・製造番号・資格情報を置かず、記録の Pull Request は人が中身を見て確かめます。

## 置かないもの

- Core のコード
- 複数案件で使う共通の判定ロジック（Core へ還元する）

# manifests/ — Dataset と Model の参照

**本体は置かず、どこに何があるかを書きます。**

- Dataset の版と置き場所、hash
- Model の版と hash
- 学習・評価に使った撮像回の一覧

## 置かないもの

- 画像・Dataset・Model Weight の**本体**（Secure Storage に置く）

Git は大量のバイナリに向きません。**参照と hash だけを持ちます。** hash があれば「あの時のデータ」を後から特定できます。

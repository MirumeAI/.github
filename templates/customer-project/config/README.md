# config/ — この案件の設定

Core へ配る設定を置きます。配る先は Core 側の据え付け手順（`setup_customer.sh`）が決めます。新しい形では、鍵ごとに `recipes/case.yaml` と `machines/` へ分けます（根の `README.md` の「目標の形と移る間」）。面ごとに判定する流れの設定（`config/pdm/` など）と門番の設定（`pdm_guard_policy.json`）は、版 1 では行き先が無く、今の置き場のまま（`MirumeAI/development-docs` の `03_customer_development/recipes_and_machines.md` の「まだ決めないこと」）。

```
config/image-acquisition/   撮像・判定側（カメラ、PLC、面、検査範囲、除外領域）
config/pdm/                 学習・評価側
```

## 置かないもの

- 開発の機体だけの設定（保存済み画像のカメラなど）。ここに置くと機体へ配られるので、`dev/` に置きます（`dev/README.md`）
- Core の Source（`PDM` と `Image-Acquisition-PDM` の中身）
- Credential、ライセンスファイル、パスワード

**検査の性質（`inspection` または `flow`）と `plc.enabled` / `ai.enabled` は必ず書きます。** 書き忘れで決まってよいことではないため、据え付け時に止まります。

設定を変えたら、根の `customer-profile.yaml`（顧客の間の混入の検査の宣言）も同じ Pull Request で直します。始め方は根の `README.md` の「顧客の間の混入の検査を始める」にあります。

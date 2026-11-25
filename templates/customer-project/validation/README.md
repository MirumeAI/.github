# validation/ — 仕様に対する技術検証

`requirements/` の仕様を満たしているかを確かめた記録を、1 回の確かめで 1 ファイル、足すだけで置きます。

```
validation/VR-<案件の記号>-<年>-<連番 3 桁>.yaml     例: VR-PJ001-2026-001.yaml
validation/protocols/<手順の ID>.md                   確かめる手順（版つき）
```

- 状態は記録から決まります（対象 × 手順の ID ごとに、一番新しい記録が PASS なら VERIFIED）。文章で状態を付けません
- 記録の Pull Request には、確かめた人の Human Verification が要ります

## 置かないもの

- 顧客の受入の記録（`acceptance/` に置く）
- 値（IP・製造番号・顧客の識別子）と秘密。大きな証拠と値を含みうる証拠はデータの置き場に置き、ID と sha256 で指す

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

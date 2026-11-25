# deployment/ — 配置した事実の記録

どの機体に、いつ、何を入れたか（棚卸しでは、何が入っていたか）を、1 回の出来事で 1 ファイル、足すだけで記録します（書き換えない）。

```
deployment/<機体の記号>-<通し番号 4 桁>.yaml     例: M01-0001.yaml
```

- 今の状態は、機体ごとに通し番号が一番大きい記録です（ファイルの更新日時では決めない）
- 戻せる先（`rollback_to`）を必ず書きます
- 記録の Pull Request には、配置した人・確かめた人の Human Verification が要ります

## 置かないもの

- 確かめた結果（`validation/` の検証の記録に書く）
- IP・製造番号・装置名（機体の記号で書く。対応は `deploy/README.md`）
- `dev/` と `MIRUME_DEV_CONFIG` を指す書き方

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`。確かめる道具: `MirumeAI/.github` の `scripts/ci/release_record_check.py`

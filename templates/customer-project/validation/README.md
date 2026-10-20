# validation/ — 仕様に対する技術検証

`requirements/` に書いた仕様を満たしているかを確かめた記録を置きます。

- 固定した評価での結果
- 結合試験（撮像 → 判定 → 設備への返し）
- 配置の後の smoke test（`deployment/` の配置の記録の ID を指す）
- 満たせていない項目とその扱い

## 検証の記録

1 回の確かめで 1 ファイル、足すだけで記録します。

```
validation/VR-<案件の記号>-<年>-<連番 3 桁>.yaml     例: VR-PJ001-2026-001.yaml
```

```yaml
schema_version: 1
verification_id: VR-PJ001-2026-001   # ファイル名と同じ
scope: deployment                    # product / system / deployment
target:
  deployment: M01-0002               # scope が deployment のとき（deployment/ の記録の ID）
  # system_release: PJxxx-R2026.10.1 # scope が system のとき
  # product: { name: pdm, tag: v0.4.0, commit: <40 桁> }   # scope が product のとき
machine: M01                         # scope が deployment のとき（配置の記録と同じ機体）
protocol: { id: SMOKE, version: 1 }  # 手順は validation/protocols/
environment: real-machine            # real-machine / bench / ci
date: 2026-10-20
verifier: { role: <役割>, account: <GitHub のアカウント> }   # 人。Agent は確かめた人にならない
result: PASS                         # PASS / FAIL / PARTIAL（項目の結果と合わせる）
cases:
  - { id: C1, result: PASS, note: <値を書かない> }
evidence:
  - { type: file, path: validation/evidence/<ファイル> }      # 値を含まない小さなファイル
  - { type: storage, id: <データの置き場の ID>, sha256: <64 桁> }
known_limitations: []
supersedes: null                     # 同じ対象と手順の、前の記録を置き換えるとき
```

- **状態は記録から決まる見え方です。** 対象 × 手順の ID ごとに、置き換えられていない一番新しい記録が PASS なら VERIFIED、FAIL なら FAILED、PARTIAL なら NOT VERIFIED。文章で状態を付けません
- 記録の Pull Request には、確かめた人の Human Verification が要ります
- 値（IP・製造番号・顧客の識別子）と秘密は書きません。大きな証拠と、値を含みうる証拠はデータの置き場に置き、ID と sha256 で指します

## 置かないもの

- 顧客の受入記録（`acceptance/` に置く）

**Validation と Acceptance は別です。** 前者は仕様に対する技術検証、後者は顧客・現場が受け入れる条件です。仕様を満たしても受け入れられないことがあります。

System Release の manifest がここを参照します。形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

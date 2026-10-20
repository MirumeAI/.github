# deployment/ — 配置した事実の記録

どの機体に、いつ、何を入れたか（棚卸しでは、何が入っていたか）を、1 回の出来事で 1 ファイル、足すだけで記録します（書き換えない）。

```
deployment/<機体の記号>-<通し番号 4 桁>.yaml     例: M01-0001.yaml・M01-0002.yaml
```

「版が存在する」ことと「この機体へ配置した」ことは別の事実です。混ぜると、次のときに答えられません。

- 1号機と2号機で判定が違う。どちらに何が入っているのか
- 不良が流出した。そのとき動いていた Model と閾値は何か
- 戻したい。**どの状態へ戻せば動くと分かっているのか**

**今の状態は、機体ごとに通し番号が一番大きい記録です。** ファイルの更新日時では決めません。

```yaml
schema_version: 2
record_id: M01-0002        # <機体の記号>-<その機体の通し番号 4 桁>。ファイル名と同じ
machine: M01               # 中立の機体の記号（IP・製造番号・装置名を書かない。実物との対応は deploy/README.md）
sequence: 2                # 機体ごとに 1 から。抜けも重なりも無し
supersedes: M01-0001       # その機体の前の記録（最初は null）
status: ACTIVE             # LEGACY（棚卸しで見た状態）/ ACTIVE（配置した状態）/ RETIRED（使わなくなった）
recorded_by: { role: <役割>, account: <GitHub のアカウント> }   # 人

# ACTIVE のとき
deployed_release: PJxxx-R2026.10.1   # release/ にある System Release
rollback_to: M01-0001                # 戻せる先（同じ機体の前の記録。LEGACY の状態でもよい）
config_commit: <40 桁>               # 配った案件の設定の commit
artifacts:
  - { name: pdm, version: 0.4.0, sha256: <64 桁> }
model_sha256: <64 桁>
rolled_back_from: null               # 戻したときは、戻す前の記録

# LEGACY のとき（棚卸しで、人が読むだけで確かめた状態）
# observed:
#   working_copies: [ { repository: <Core>, commit: <40 桁>, modified: false } ]
#   packages: [ { name: <名前>, version: <版>, sha256: <64 桁> } ]
#   config_sha256: <64 桁>
#   model_sha256: <64 桁>
```

- 確かめた結果（smoke test・受入）はここに書きません。`validation/` の検証の記録に書き、検証の記録がこの記録の ID を指します
- 戻せる先（`rollback_to`）は**必ず書きます**。書けないなら、戻せる状態になっていないということです
- 記録の Pull Request には、配置した人・確かめた人の Human Verification が要ります
- 前の形（`schema_version: 1`。`<site>-<line>-<日付>.yaml`）の記録も、確かめる道具は読みます

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`。確かめる道具: `MirumeAI/.github` の `scripts/ci/release_record_check.py`

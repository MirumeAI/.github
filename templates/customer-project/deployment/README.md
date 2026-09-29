# deployment/ — 配置した事実の記録

どの機体へ、いつ、何を入れたかを1ファイル1回で記録します。

```
deployment/<site>-<line>-<日付>.yaml     例: line-1-2026-10-04.yaml
```

「版が存在する」ことと「この機体へ配置した」ことは別の事実です。混ぜると、次のときに答えられません。

- 1号機と2号機で判定が違う。どちらに何が入っているのか
- 不良が流出した。そのとき動いていた Model と閾値は何か
- 戻したい。**どの版へ戻せば動くと分かっているのか**

`rollback_release` は**必ず書きます**。書けないなら、戻せる状態になっていないということです。

形式の正本: `MirumeAI/development-docs` の `03_customer_development/release_and_deployment.md`

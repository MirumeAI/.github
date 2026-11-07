# plugins/ — Core に差し込む、この案件固有の拡張

差し込む Core の入口ができた後に、案件固有の拡張（例: 別の通信方式の PLC の Adapter）をここへ置きます。Core の公開の面を使う製品アプリ（起動・製品画面・API）は `app/` に置きます。

**汎用の Plugin の仕組みはありません。** 差し込む入口は、具体の要求が出たときに Architecture Review で 1 つずつ作ります（`MirumeAI/development-docs` の `03_customer_development/customer_specific_development.md` の「Core の入口」）。ここのコードから Core の内部を直に import しないでください。入口ができた後も、使うのは公開の面と入口だけです（Core を変えるたびに壊れ、Core の写しを持つのと同じになります）。

## 置かないもの

- Core に入れるべき機能（2案件以上で使うものは Core へ還元する）
- Core 内に顧客名で分岐を書くこと
- Core の内部を直に import するコード

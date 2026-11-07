# app/ — 製品アプリ

この案件の製品アプリ（起動・製品画面・API・Adapter）を置きます。Core の公開の面ができた範囲から置けます（`MirumeAI/development-docs` の `03_customer_development/customer_specific_development.md` の「顧客 Repository での開発」と `platform_evolution.md`）。

## 使ってよいもの

- 各 Core の `docs/public_api.md` に書かれた公開の面（起動の入口・コマンド・HTTP・Python の部品・環境変数・画面の部品）だけ
- 製品アプリは、公開の面に書かれた Core の Web アプリの入口を読み込み、外から API と画面を足します
- 使う Core の版は `versions.yaml`（開発中は `versions.dev.yaml`）で選びます

公開の面に無いものが要るときは、Core の内部を使わずに、Core の Issue（改善依頼）を作ります（同じ文書の 4 章）。

## 守ること

- **Core の内部を import しない。** Core を直すたびに製品が壊れ、Core の写しを持つのと同じになります
- **認証とライセンスの門番を外さず、迂回しない。** PDM 側の製品の API と画面は門番の内側に置きます。入口を開く・`allow_ports` を広げる変更は、守りの境として人の判断が要ります
- 判定の結果は、判定できない・エラー・知らない状態を良品にしない（撮像側の `control.inspection.result` の形を使う）
- 開発の機体で案件を固定するときは `MIRUME_DEV_CONFIG` を使います。据え付けの機体は `INSPECTION_CONFIG` です

## 置かないもの

- Core の Source とその写し
- ほかの案件の値・秘密・資格情報・ライセンスファイル
- Core に入れるべき汎用の機能（2 件目の案件でも要るものは Core へ還元する）

製品アプリのコードは、顧客の間の混入の検査の範囲の外です。範囲が広がるまでは、製品アプリの Pull Request で、ほかの案件の値・秘密・資格情報・Core の写し・Core の内部の使用・門番の迂回が入っていないかを Reviewer B が見ます。

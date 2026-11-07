# app/ — 製品アプリ

この案件の製品アプリ（起動・製品画面・API・Adapter）を置きます。Core の公開の面ができた範囲から置けます（`MirumeAI/development-docs` の `03_customer_development/customer_specific_development.md` の「顧客 Repository での開発」と `platform_evolution.md`）。

## 使ってよいもの

- 各 Core の `docs/public_api.md` に書かれた公開の面（起動の入口・コマンド・HTTP・Python の部品・環境変数・画面の部品）だけ
- 製品アプリは、公開の面に書かれた Core の Web アプリの入口を読み込み、外から API と画面を足します
- 出荷の組合せは `versions.yaml` で選びます。開発中に Core の直しを試すときは、`versions.dev.yaml` に Core ごとの commit を置けます（選択ではなく、機体に配置しません）

公開の面に無いものが要るときは、Core の内部を使わずに、この Repository に改善依頼を書きます（顧客の名前と値を書かない形で）。共通プラットフォームの側が分類し、Core に足すものは Core の Issue にします（`platform_evolution.md` の 4 章）。Core の直しを待つ間は、製品の側の Adapter で進められないかを先に見ます。

## 守ること

- **Core の内部を import しない。** Core を直すたびに製品が壊れ、Core の写しを持つのと同じになります
- **認証とライセンスの門番を外さず、迂回しない。** 製品アプリは PDM の Web アプリを直に開かず、門番 `pdm_guard.app:app` を通します。PDM 側の製品の API と画面は門番の内側に置きます。入口を開く・`allow_ports` を広げる変更は、守りの境として人の判断が要ります
- 検査側の製品の API と画面は、今の検査の Web アプリと同じ扱いです（認証は無く、別サイトからの要求を断る守りだけです）。副作用のある入口は POST にします
- 判定の結果は、判定できない・エラー・知らない状態を良品にしません（撮像側の `docs/public_api.md` の Python の部品 `control.inspection.result` が、この扱いを持つ形です）
- 開発の機体で案件を固定するときは `MIRUME_DEV_CONFIG` を使います。顧客に見せる機体を含め、開発の機体でないものでは使いません。据え付けた機体の製品の起動は `INSPECTION_CONFIG` を必ず渡します（無いと、デモと保存済み画像のカメラの断りが効きません。両方あるときは `INSPECTION_CONFIG` が先です）

## 置かないもの

- Core の Source とその写し
- ほかの案件の値・秘密・資格情報・ライセンスファイル
- Core に入れるべき汎用の機能（2 件目の案件でも要るものは Core へ還元する）

製品アプリのコードは、顧客の間の混入の検査の範囲の外です。範囲が広がるまでは、製品アプリの Pull Request で、ほかの案件の値・秘密・資格情報・Core の写し・Core の内部の使用・門番の迂回が入っていないかを Reviewer B が見ます。

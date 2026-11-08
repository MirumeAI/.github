---
description: 顧客からの変更要求を対象Componentと実装分類の二段階で判断する
argument-hint: [顧客要求の内容]
---

顧客要求「$ARGUMENTS」を、MirumeAI の二段階ルールで分類してください。
判断根拠を示し、結論を断定できない場合は Architecture Review とします。

## Step 1 対象Component

| 対象 | 判断の目安 |
|---|---|
| `PDM` | Platform、Web、UI、システム管理、製品・検査管理 |
| `Image-Acquisition-PDM` | Camera撮像、検査実行、判定、Hardware 実行連携 |
| `Both` | 二つの Core または Core 間契約に変更が必要 |
| `Customer only` | Core を変えず顧客固有差分だけで対応（設定・記録・製品アプリ。製品アプリは Core の公開の面の範囲だけ） |

両 Core の責務境界は未確定です。コードを確認せずに断定せず、不明なら Architecture Review とする。

## Step 2 実装分類

```
設定値だけの変更か
  ├─ YES → Config
  └─ NO
       ↓
  顧客名を除いても一般製品機能として成立するか
       ├─ YES → Core / Core + Config
       └─ NO
            ↓
       特定顧客・PLC・設備・API 依存か
            ├─ YES → Plugin・Adapter 候補
            └─ NO  → Architecture Review
```

`Plugin・Adapter` 候補でも、Core 側の拡張 Interface は**今は無い**（2026-11-04 に確かめた）。要るときは、具体の要求で Architecture Review をして入口を 1 つずつ作る。

製品アプリ（起動・製品画面・API・Adapter）は顧客 Repository に置き、両 Core の `docs/public_api.md` の公開の面と入口だけを使う。Core の内部は、公開の面や入口ができた後も直に import しない。認証とライセンスの門番を外さず、迂回しない。公開の面に無いものが要るときは、Core の Issue を作る（development-docs の `03_customer_development/customer_specific_development.md` の「顧客 Repository での開発」「Core の入口」）。

## 出力

1. 対象Component と判断根拠
2. 実装分類と判断根拠
3. 確認すべきコード・設定の場所（製品アプリに置くときは、使う公開の面の項目）
4. `Both` の場合: 顧客 Repository の Issue（1 つ）・各 Core の Pull Request・各 Core の Product Release（タグは人が打つ）・顧客 Repository の `versions.yaml` で選ぶ組合せ（撮像側の lock と合うことを確かめる）の構成案。Core 側で独立した Issue が要るのは、その Core 単独で意味のある変更になり他の顧客にも影響する場合と、Core の公開 Interface を変える場合（Architecture Review の対象）だけ（development-docs の `03_customer_development/customer_specific_development.md` の「Bothの場合」）
5. Core へ還元すべき共通機能があるか。次のどれかに当たれば、Core の Issue を作る: 2 件目の案件でも同じものが要る・Core の内部を直に使わないと作れない・System Release を作るときの見直しで上げられると分かった。還元の Pull Request では、顧客の値が Core に入らないことを、各 Core の混入の検査と Reviewer B で確かめる（同じ文書の「Coreへの還元」）
6. Architecture Review が必要なら、決めるべき論点の一覧

## 禁止事項に該当しないか確認する

- Core Source 全体のコピー
- Core 内の顧客名による `if` 分岐
- Core Repository に顧客別の長期 Branch を新しく作ること（今ある長期 Branch は LEGACY として移行まで残す）
- 顧客 Repository のコードから Core の内部を直に import すること
- 製品アプリが認証とライセンスの門番を外す・迂回すること
- 顧客固有 Patch を恒久的に放置すること
- PDM / Image-Acquisition-PDM の Version が不明な状態

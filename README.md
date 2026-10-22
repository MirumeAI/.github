# MirumeAI GitHub Default Templates

MirumeAI Organization 配下の Repository で共通利用する Issue / Pull Request テンプレートです。

> **重要**
> この Repository (`MirumeAI/.github`) は Public で運用します。
> 顧客名、社内URL、IPアドレス、秘密情報、具体的な認証情報などは記載しないでください。

## Issue Forms

- `Feature` : 新機能・機能改善・ドキュメント改善
- `Bug` : 不具合修正
- `Experiment` : AI・アルゴリズム・評価実験

## 基本フロー

1. Issue を作成する
2. Issue の目的と完了条件をチーム内で共有する
3. Issue 番号を含む Branch を作成する
4. 実装・検証する
5. Pull Request を作成する
6. merge する head で、手元の検査を流して PASS を PR に記録する（下の「merge の前の検査」）
7. 原則 Squash and merge する（レビュー可能なメンバーがいる場合は Review 後）
8. Issue を Close する

### Branch 命名例

- `feature/42-camera-reconnect`
- `fix/51-login-error`
- `experiment/73-patchcore-backbone`

## 適用範囲

この `.github` Repository のテンプレートは、MirumeAI Organization 配下の各 Repository に同種の独自テンプレートがない場合に既定値として利用されます。

## merge の前の検査（`run_required_verification.py`）

GitHub Actions は、Private の Repository では使いません（この Repository だけ動きます）。merge の前提は、**PR で動く workflow の段を、merge する head で手元で流して PASS すること**（Required Verification）です。命令を手で組み立てず、この道具で流します。

```bash
git -C <この Repository の clone> pull --ff-only     # 正本の道具を最新にする
git -C <検査する Repository の clone> fetch origin
python3 <この Repository の clone>/scripts/ci/run_required_verification.py \
    <検査する Repository の clone> --head <PR の head の commit>
```

- 検査する Repository の `.github/workflows/` から、`pull_request` で動く workflow を選びます（`paths`・`branches`・`types` の条件も、`origin/main` との merge-base からの差で見ます）。job と段は CI と同じ順で流します。再利用 workflow と正本の道具は、この clone の `origin/main` から取ります
- job ごとに、新しいものを 3 つ用意して流します
  - head の新しい clone
  - workflow が求める版の Python の新しい venv（`python3.10` などを PATH から探します。無ければ `--python 3.10=<道>` で渡します）
  - 仮の HOME
- 元の clone と HOME は変えません。環境変数は決めた名前（`PATH`・`LANG` など）だけを渡し、token・ssh の agent・git の設定などは渡しません。段が終わったら、後ろで動き続ける子の process も止めます（`setsid` で抜けたものは止められません）。pip で依存を入れるので、network が要ります
- 知らない書き方（action・式・matrix・`defaults`・`pull_request_target`・再利用 workflow の入力の式など）があれば、何も流さずに止まります（終了コード 2）。落ちた段があれば FAIL です（終了コード 1）
- PASS のときに出る「記録の行」（`GitHub Actions unavailable — canonical verification PASS（…）`）を、そのまま PR に書きます。記録の行には、次のものが入ります
  - head と、基準（`origin/main`）を含むか
  - 正本の ref と commit
  - 流した道具の版（正本と同じか）
  - 段・試験・飛ばした試験の件数
- 記録の行を見るときの決まり
  - 「基準を含む: いいえ」なら、merge の前に main を取り込んで流し直します（GitHub の `pull_request` は、main と合わせた commit で流すため）
  - 飛ばした試験が 0 でなければ、理由を確かめます
- `--list` を付けると、流す workflow・job・段を出すだけです。PyYAML が無ければ、道具用の venv を `~/.cache/mirumeai/` に作って入れます
- **PR のコードを手元で動かします。** 中身を読んでから流してください
  - 仮の HOME と環境変数の取り除きは、手元の設定や認証をうっかり使わせないためのもので、悪意のあるコードを閉じ込める箱ではありません
  - PR のコードと pip で入る依存は、あなたの OS のユーザーの権限で動きます。本物の HOME の認証の file や手元のデータを読め、network にも出せます

## 顧客の間の混入の検査（`customer-profile.yaml`）

顧客 Repository の設定に、別の案件の値・ファイル・構成が混ざっていないかを、その案件自身の宣言（Repository の根の `customer-profile.yaml`）と照らします（`scripts/ci/customer_isolation_check.py`）。ほかの顧客の値も、顧客の名前の登録表も、secret も使いません。

有効にするのは、宣言を置いた顧客 Repository だけです（既定は `false`。指定しない Repository は何も変わりません）。

新しい顧客 Repository は、作る道具（`scripts/ci/new_customer_repo.py`）が雛形から、宣言の**値の無い初期形**（状態は人の確認待ち）と `run-isolation-check: false` を入れて始まります。初期形のままでは検査が照らせずに止まり（終了コード 2）、合格になりません。始め方（設定 → 宣言の下書き → 報告だけの形 → 人の確認 → VERIFIED → 有効化）は雛形の README（`templates/customer-project/README.md`）にあります。

```yaml
jobs:
  checks:
    uses: MirumeAI/.github/.github/workflows/basic-checks-reusable.yml@main
    with:
      run-isolation-check: true
```

手元で流すには PyYAML が要ります。

```bash
python -m pip install pyyaml
python scripts/ci/customer_isolation_check.py --root <顧客 Repository> --report-only
```

| 終了コード | 意味 |
|---|---|
| 0 | 合格（`--report-only` のときは、混入の疑いがあっても 0） |
| 1 | 混入の疑い（宣言の外の値・ファイル・構成） |
| 2 | 宣言・設定・検査の誤り（宣言が無い・読めない・資格情報らしい鍵がある、設定の JSON が読めない・同じ鍵がある、PyYAML が無い など）。照らせないときは合格にしません |

見るもの:

- 層 1: カメラの IP と製造番号、PLC の宛先と番地、トリガを決まった場所から取り出し、宣言と照らす。補いとして、決まった場所の外の IPv4 と `trig_` で始まる語も照らす（JSON の鍵と値、`.txt` の中、ファイルの名前）
- S1: `config/` の下は、Core が読む決まった名前と、宣言した追加のファイルだけ（説明の `.md` と空の `.gitkeep` は除く）
  - `config/image-acquisition/`: `config.json`・`pdm_guard_policy.json`・`recipe.json`・`machine.json` と、宣言した面の `<面>.json`・`<面>__region*.json`（面は、面の名前か、末尾の `_<数字>` を落とした基の名前）
  - `config/pdm/`: 面ごとの設定の置き場（`adaptive_mask`・`align`・`bbox_acquire`・`good_pool`・`region_split`。ファイルの名前が宣言の面か）と `align_reference.txt`。`defaults/` は宣言しても許しません
- S2: PLC・NAS・AI を使うかが宣言と同じか。PLC を使わないなら、`plc` は `{"enabled": false}` だけ。`nas.enabled` を書いていなければ、照らさずにそう言います（推測しません）
- S3: 構成の名前（`_profile`）・検査の性質（`flow` か `inspection` の一方）・カメラ・面が宣言の中か
- S4: `deploy/` と `versions.yaml` が触れる設定のファイル（`config/...` の道。`./`・`../` で始まるものも含む。それと `config` で始まる `.json` の名前）が、実在して許されているか
- S5: 記録（`deployment/`・`validation/`。配置の記録・検証の記録と、その手順と証拠のファイル）。記録には値を書かないので、宣言に無い IPv4 と `trig_` で始まる語は不合格。参考だけにするのは雛形の 3 つの `README.md`（`deployment/`・`validation/`・`validation/protocols/`）の中だけで、手順と証拠の `.md` も照らす。鍵 `version` の値（package の版）は IPv4 とみなさない。空でない `.gitkeep` も照らす。下のリンクは読まずに断る。検証の記録の ID の案件の記号（`validation/VR-<記号>-<年>-<連番>.yaml`）が、記録どうしと、`release/` の System Release の ID の記号と同じか
- 参考（不合格にしない）: `deploy/` の中の値と、説明の `.md` の中の値（記録の置き場では、雛形の 3 つの `README.md` だけ）。強制するかは、既存の案件で報告だけの形で流した結果を見て決めます

宣言の例（値は文書用の架空のもの）:

<!-- customer-profile.yaml の例 -->
```yaml
schema_version: 1
project:
  flow: example-flow             # config.json の flow と同じ（inspection で書く案件は inspection: に軸ごと）
  profiles: [example-site]       # config.json の _profile に使ってよい名前
capabilities:                    # 使うかどうか（推測しないので、必ず書く）
  plc: true
  nas: false
  ai: true
equipment:
  cameras:
    - {id: camera1, ip: 192.0.2.11, serial: EXAMPLE-SN-0001}
    - {id: camera2, ip: 192.0.2.12, serial: EXAMPLE-SN-0002}
endpoints:                       # カメラと PLC のほかの宛先。ちょうどの値で書く
  - 192.0.2.50
  - network: 192.0.2.64/28       # 網は本当に要るときだけ。理由を書き、/24 より広い網は書けない（32 個まで）
    reason: 架空の例。機器の IP が DHCP で変わる
plc:
  endpoints: ["192.0.2.5:5000"]
  addresses: [EXAMPLE_D100, EXAMPLE_D101]
triggers: [trig_example_top, trig_example_side]
faces: [trig_example_top, trig_example_side]
config_files:
  extra:                         # Core が読む決まった名前の外に置くファイル。理由を書く
    - path: config/image-acquisition/example_extra.json
      reason: 架空の例
extensions: []                   # plugins/ の拡張の名前（今は数えて増減を出すだけで、照らさない）
```

- 宣言には、その案件の自分の値だけを書きます。資格情報（パスワード・トークン・鍵）は書きません（書くと終了コード 2）
- 鍵は文字で書きます。数で書く値（製造番号など）は引用符で囲みます（YAML では先頭の 0 などが消えます）
- `127.` と `255.` で始まる IP、`0.0.0.0`、Core の鍵 `trig_bit`・`trig_ready` は宣言しなくて構いません。文書用の網（`192.0.2.x` など）は除きません
- 値は出力しません。出すのは種類・ファイル・行・欄だけです（決まっていない名前は `<*>` と `<伏せ#n>`）
- PR では、基準の commit の宣言と比べた増減の数を出します。増えていれば `PROFILE EXPANSION DETECTED` と出る（Actions では注意としても出る）ので、増えた値が本当にこの案件のものかをレビューで確かめてください（設定と宣言の両方へ同じ誤った値を足すと、照合では見つかりません）
- **これは事故を止める検査で、悪意のある変更を止める境界ではありません。** PR の CI は PR の側の workflow で動くので、PR はこの検査を外せます。宣言と workflow の変更はレビューで見ます

## Product Release の識別の確かめ（`release_identity_check.py`）

各 Core の Release の workflow が、 Product Release を作る前に使う共通の確かめです。**版の文字・注釈つきの動かさないタグ・そのタグが指す commit を 1 対 1 に対応させます。** 決まりの正本は development-docs の「ReleaseとDeploymentの記録」の「Product Release の識別」です。

```bash
# タグの push を受けた CI（落ちたら Release を作らない）
python scripts/ci/release_identity_check.py check --repo-root <Core> --tag vX.Y.Z \
    --version-source pyproject --first-version X.Y.Z --releases-json releases.json
# 人がタグを打つ前（タグは打たない）
python scripts/ci/release_identity_check.py preflight --repo-root <Core> --tag vX.Y.Z \
    --commit <40 桁の SHA> --version-source VERSION --first-version X.Y.Z --remote origin
```

見るもの: タグの形（`vX.Y.Z` だけ）・`--first-version` より前の番号（LEGACY）でないこと・注釈つきのタグ・タグの commit が main の祖先・版の出どころ（`pyproject.toml` の `[project].version` か `VERSION`）との完全な一致・開発の版（`X.Y.Z.devN`）でないこと・前の Product Release（LEGACY を使わない）・Release のノートに記録したタグと commit が今も同じか。

- 顧客の値と名前は扱いません。形の違うタグの名前は出しません
- Release のノートは識別だけ（product・version・tag・tag object・commit・CI の実行・成果物の状態）で、commit の件名は載せません
- タグを打つのは Core の maintainer（人）だけです。手元の guard は、 Agent のタグの作成・削除・付け替えとタグの push を止めます

| 終了コード | 意味 |
|---|---|
| 0 | 合格 |
| 1 | 不合格（理由の種類だけを出す） |
| 2 | 使い方の誤り |

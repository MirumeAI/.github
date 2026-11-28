# 共通の検査の道具（`scripts/ci/`）

MirumeAI の Repository が共通に使う検査の道具と、その試験（`test_*.py`・`test_*.sh`）の正本です。各道具の引数と終了コードの細部は、道具の冒頭の説明と `--help` にあります。ここには、道具の一覧と、よく使う道具の流し方をまとめます。

各 Repository へ写す道具の一覧は `copy_sync_check.py` の `SHARED` にあります（写しは各 Repository で直さない。[README](../../README.md) の「変えるとき」）。

## 道具の一覧

| 道具 | 役目（冒頭の説明の初めの文） |
|---|---|
| `run_required_verification.py` | merge の前提の検査（Required Verification）を、PR で動く workflow と同じ段で手元で流す。 |
| `gate_record.py` | Reviewer への頼み・Merge の門の記録・merge の後の記録・Pull Request の本文の記録の行を、Required Verification の記録と git・Pull Request の状態から作る。 |
| `basic_checks.py` | Pull Request の機械チェック。 |
| `import_check.py` | import しているのに宣言していない依存を見つける。 |
| `customer_data_check.py` | 追跡しているファイルに、現場の値が混ざっていないかを見る。 |
| `customer_isolation_check.py` | 顧客リポジトリの設定に、別の案件の値が混ざっていないかを、その案件自身の宣言（リポジトリの根の `customer-profile.yaml`）と照らして確かめる。 |
| `copy_sync_check.py` | 手元用の複製が、正本とずれていないことを確かめる。 |
| `sync_copies.py` | 写しを持つ Repository へ、正本（この Repository）の共通のファイルを合わせる Pull Request を、まとめて作る。 |
| `case_cache.py` | 案件のフォルダの `.cache/` と、共通の道具の cache（利用者の cache の下の `mirumeai/`）の、使い終えた物を片付ける（既定は見るだけ。`--apply` で消す）。 |
| `mutation_engine.py` | 変異の確かめの仕組み。テストが本当に検証しているかを、守りを壊して確かめる。 |
| `release_identity_check.py` | Product Release の識別を確かめる（各 Core の Release の workflow が使う共通の確かめ）。 |
| `release_record_check.py` | Release と Deployment の記録が形式どおりか確かめる。 |
| `new_customer_repo.py` | 顧客案件の Repository を作る。Organization の Owner が実行する。 |
| `org_audit.py` | Organization の設定差分（Governance Drift）を読み取りで監査する。 |

## merge の前の検査（`run_required_verification.py`）

Private の Repository では GitHub Actions を使いません。merge の前に、PR で動く workflow の段を、merge する head で手元で流して PASS させます（Required Verification）。命令を手で組み立てず、この道具で流します。流し方の細部は道具の冒頭、決まりは development-docs の `02_github_workflow/pull_request_review.md` の「検査と Merge」にあります。

```bash
git -C <この Repository の clone> pull --ff-only     # 正本の道具を最新にする
git -C <検査する Repository の clone> fetch origin
python3 <この Repository の clone>/scripts/ci/run_required_verification.py \
    <検査する Repository の clone> --head <PR の head の commit>
```

- PASS のときに出る記録の行（`GitHub Actions unavailable — canonical verification PASS（…）`）を、そのまま PR に書きます。「基準を含む: いいえ」なら main を取り込んで流し直し、飛ばした試験が 0 でなければ理由を確かめます
- 終了コードは 0 が PASS、1 が FAIL、2 が流せない（知らない書き方・Python の版が無いなど）です。`--list` は流す段を出すだけです
- 同じ利用者の検査は重ねません。ほかの検査が流れていれば、その Repository（別の顧客 Repository なら名前を出さない）・head・始めた時刻を出して終わるまで待ちます（`--no-wait` なら待たずに終了コード 2）。同時に流す job の数は、待ち終えた後に決めます。止めたときと落ちたときも、ロックは残りません
- 同時に流す job の数（`--jobs`）は、渡さなければ機械に合わせて決め、数と根拠を出します（使える CPU の数 − 2 と、空きメモリ ÷ 2 GiB の小さい方）
- **PR のコードを手元で動かします。** 中身を読んでから流してください。仮の HOME と環境変数の取り除きは、悪意のあるコードを閉じ込める箱ではありません

## 門の記録と頼みの文（`gate_record.py`）

Reviewer への頼み・Merge の門の記録・merge の後の記録・PR の本文の記録の行を、検査の記録と git・PR の状態から作ります。**文を作るだけで、GitHub へ書き込みません。** 投稿と merge は、中身を確かめてから行います。

```bash
G=<この Repository の clone>/scripts/ci/gate_record.py
python3 $G review <作業ツリー> --pr 42 --issue 41 --role a --rv-log <検査の出力>   # 頼み（B は --role b と --human）
python3 $G body <作業ツリー> --pr 42 --rv-log <検査の出力> --out body.md           # 本文の記録の行の差し替え
python3 $G gate <作業ツリー> --pr 42 --rv-log <検査の出力> --record record.json --out gate.md
python3 $G merged <作業ツリー> --pr 42 --head <固定した head> --out merged.md
```

- `review` の `--look`・`--fixed`・`--human` には、人の判断・人の確認の言葉に触れるだけの文を書けます。記録の形（印の言葉の後に `:` などの区切りや、`PASS`・`承認` などの結果の言葉が来る形）は止まります。止まったら `が完了したかを確かめる`・`の完了を待つか` のように言い直します
- `gate` は、機械で確かめられる門の条件（head と検査の記録・main の進み・Reviewer の結果・In-PR の残り・人の判断の言葉がこの PR と今の head を名指ししているか）が 1 つでも外れたら止まります。通れば、門の記録と head を固定した merge の命令を作ります。`--record` の形は道具の冒頭にあります
- 道具が見ない門の条件と、人の確認（Human Verification）が要るかは、Agent が確かめてから merge します。base が main でない PR と、人の確認が要る PR は、この道具の外です

## 顧客の間の混入の検査（`customer-profile.yaml`）

顧客 Repository の設定に、別の案件の値・ファイル・構成が混ざっていないかを、その案件自身の宣言（根の `customer-profile.yaml`）と照らします（`scripts/ci/customer_isolation_check.py`）。有効にするのは、宣言を置いた顧客 Repository だけです（再利用 workflow の入力 `run-isolation-check: true`。既定は `false`）。新しい顧客 Repository は宣言の値の無い初期形で始まり、始め方は雛形の `templates/customer-project/README.md` にあります。

```bash
python scripts/ci/customer_isolation_check.py --root <顧客 Repository> --report-only
```

PyYAML が要ります。終了コードは 0 が合格（`--report-only` のときは、混入の疑いがあっても 0。最後の行を見る）、1 が混入の疑い、2 が宣言・設定・検査の誤り（照らせないときは合格にしない）です。見るもの（層 1・S1〜S6、新しい形のレシピと機体の設定）は道具の冒頭、運用の決まり（人の確認・宣言の増え方・有効にする順）は development-docs の `03_customer_development/customer_isolation_check.md` にあります。宣言は次の例のように書きます（値は文書用の架空のもの）。

<!-- customer-profile.yaml の例 -->
```yaml
schema_version: 1
project:
  flow: example-flow             # config.json の flow と同じ（inspection で書く案件は inspection: に軸ごと）
  profiles: [example-site]       # config.json の _profile に使ってよい名前
capabilities: {plc: true, nas: false, ai: true}   # 使うかどうか（推測しないので、必ず書く）
equipment:
  cameras:
    - {id: camera1, ip: 192.0.2.11, serial: EXAMPLE-SN-0001}
    - {id: camera2, ip: 192.0.2.12, serial: EXAMPLE-SN-0002}
endpoints:                       # カメラと PLC のほかの宛先。ちょうどの値で書く
  - 192.0.2.50
  - network: 192.0.2.64/28       # 網は本当に要るときだけ。理由を書き、/24 より広い網は書けない（32 個まで）
    reason: 架空の例。機器の IP が DHCP で変わる
plc: {endpoints: ["192.0.2.5:5000"], addresses: [EXAMPLE_D100, EXAMPLE_D101]}
triggers: [trig_example_top, trig_example_side]
faces: [trig_example_top, trig_example_side]
products: [example_product]      # 新しい形の recipes/products/<製品>.yaml のファイルの名前と product
config_files:
  extra:                         # Core が読む決まった名前の外に置くファイル。理由を書く
    - {path: config/image-acquisition/example_extra.json, reason: 架空の例}
extensions: []                   # 差し込む部品の名前（今は数えて増減を出すだけで、照らさない）
```

- その案件の自分の値だけを書きます（注釈にも、ほかの案件の値を書きません。どの検査も見つけません）。資格情報（パスワード・トークン・鍵）は、値にも注釈にも書きません（値にあると終了コード 2。注釈は検査が読みません）
- 鍵は文字で書き、数で書く値（製造番号など）は引用符で囲みます（YAML では先頭の 0 などが消えます）
- `127.`・`255.` で始まる IP、`0.0.0.0`、Core の鍵 `trig_bit`・`trig_ready` は宣言しなくて構いません。文書用の網（`192.0.2.x` など）は除きません

## Product Release の識別の確かめ（`release_identity_check.py`）

各 Core の Product Release で、版の文字・注釈つきの動かさないタグ・そのタグが指す commit が 1 対 1 に対応するかを確かめます（`preflight` は人がタグを打つ前、`check` はタグの後）。タグを打つのは Core の maintainer（人）だけです。決まりの正本は development-docs の `03_customer_development/release_and_deployment.md` の「Product Release の識別」です。

## 詳しい文書

- Required Verification と Merge の門の決まり: development-docs の `02_github_workflow/pull_request_review.md`
- 顧客の間の混入の検査の運用（人の確認・宣言の増え方・有効にする順）: development-docs の `03_customer_development/customer_isolation_check.md`
- Product Release の識別: development-docs の `03_customer_development/release_and_deployment.md` の「Product Release の識別」
- 写しの同期: development-docs の `01_getting_started/claude_code_admin.md` の「写しを同期する」

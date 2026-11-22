# MirumeAI GitHub Default Templates

MirumeAI Organization 配下の Repository で共通に使うものの正本です。Issue / Pull Request テンプレートのほか、ガード・共通の command・共通の検査の道具・顧客案件の Repository の雛形を置きます（下の「この Repository の中身」）。

> **重要**
> この Repository (`MirumeAI/.github`) は Public で運用します。
> 顧客名、社内URL、IPアドレス、秘密情報、具体的な認証情報などは記載しないでください。

## この Repository の中身（正本の地図）

| 場所 | 中身 | 使われ方 |
|---|---|---|
| `.github/ISSUE_TEMPLATE/` | Issue Form（Feature・Bug・Experiment）と、Issue を作る画面の設定（`config.yml`。白紙の Issue を止め、脆弱性は非公開の報告へ、困ったときは `SUPPORT.md` へ案内する） | Organization の各 Repository の既定（下の「適用範囲」） |
| `.github/PULL_REQUEST_TEMPLATE.md` | Pull Request のテンプレート | 同上 |
| `SECURITY.md` | 脆弱性と秘密の漏れの報告のしかた（GitHub の非公開の脆弱性報告で受ける） | 同上 |
| `SUPPORT.md` | 困ったときの案内（メンバーは入口ページの「困ったとき」へ。文書の分かりにくさは development-docs の Issue へ） | 同上 |
| `.github/VULNERABILITY_REPORT.yml` | 非公開の脆弱性報告のフォーム（書かないものの注意と欄） | 同上 |
| `.claude/hooks/guard-main.sh` | ガード（作業 Branch〔`feature/`・`fix/`・`experiment/`〕でない Branch〔`main`・LEGACY の長期 Branch など〕への直接の commit・push・削除や、ガードと設定の書き換えなどを拒否する） | 各 Repository に写す |
| `.claude/commands/` | 共通の command（`/issue`・`/branch`・`/pr`・`/docs-sync`・`/customer-request`） | 各 Repository に写す |
| `.claude/scripts/issue-form.sh` | Issue Form の雛形と、本文の形の確かめ | 各 Repository に写す |
| `.claude/settings.json` | この Repository のガードのフックと権限の設定 | 新しい顧客案件の Repository の設定の元。各 Repository の設定は、それぞれの Repository が持つ |
| `scripts/ci/` | 共通の検査の道具（機械チェック・import の確かめ・ガードの回帰テスト・顧客の値の混入・顧客の間の混入・写しの照合・Release の識別と記録・Organization の監査・merge の前の検査・門の記録の文・各 Core の変異の確かめの仕組みなど）と、その試験 | 再利用 workflow から呼ぶもの（`basic_checks.py`・`import_check.py`・`test_guard_main.sh`・`copy_sync_check.py`・`customer_isolation_check.py`・`release_record_check.py`）、各 Core の workflow が写しを流すもの（`customer_data_check.py`。変異の確かめの仕組み `mutation_engine.py` とその試験 `test_mutation_engine.py` も。各 Core の `mutation_check.py` は、変異の表と Core の設定と入口だけを持ち、この仕組みを呼ぶ）、各 Core のタグの確かめに使うもの（`release_identity_check.py`。今は Actions を使わないので、タグの後に Agent が手元で流す）、手で流すもの（`run_required_verification.py`・`gate_record.py`・`org_audit.py`・`new_customer_repo.py`・`sync_copies.py`）がある。`basic_checks.py`・`import_check.py`・`test_guard_main.sh`・`customer_data_check.py` は各 Repository に写す。`mutation_engine.py`・`test_mutation_engine.py` は各 Core に写す（顧客案件には写さない）。`release_record_check.py` は顧客案件の Repository に写す（起動の道具が `--dev-pin` で開発の固定の形を確かめる。再利用 workflow は Pull Request の基準の commit を `--base-ref` で渡し、その Pull Request で足した System Release を照らす） |
| `.github/workflows/basic-checks-reusable.yml` | 各 Repository が呼ぶ再利用 workflow（共通の検査の段） | 各 Repository の workflow から呼ぶ |
| `.github/workflows/basic-checks.yml` | この Repository 自身の検査 | この Repository |
| `templates/customer-project/` | 顧客案件の Repository の雛形 | `scripts/ci/new_customer_repo.py` が使う |

- **写しは、各 Repository で直しません。** この Repository の Pull Request で正本を変え、merge の後に、各 Repository へ同期の Pull Request で合わせます。写しが正本と同じかは、共通の検査（`scripts/ci/copy_sync_check.py`）が照らします。同期の Pull Request は、`scripts/ci/sync_copies.py` でまとめて作ります（既定は計画の表示だけ。`apply --source <この Repository の Pull Request の番号>` で、Issue・作業 Branch・commit・merge の前の検査・Draft の Pull Request まで進める。merge はしない。merge の後は `cleanup --source <番号>` で、merge された同期の Pull Request の作業ツリー・ローカルの作業 Branch・検査の作業の置き場（伏せていない段ごとの記録）を片付ける。Pull Request ができる前に止まった同期と、merge されずに閉じた同期は、検査の作業の置き場だけを片付ける。同じ `--source` の `apply` と `cleanup` は同時に流せない。顧客 Repository の名前は出力に出さない。状態の置き場の中身はそのまま表示・一覧しない）
- 守りの境に当たる変更（ガード `.claude/hooks/` とその回帰テスト、`.claude/settings.json` のフックと禁止、CI の守りの検査〔秘密の走査・顧客の値の混入・顧客の間の混入・写しの照合と `.ci-copy-allow`・Release の識別〕とそれを走らせる workflow など）を変える Pull Request は、merge の前に人の判断が要ります（一覧の正本は、development-docs の `02_github_workflow/pull_request_review.md` の「人の判断が要る変更」）
- 開発の手順と判断の決まり（Merge の門など）の正本は、Private の `MirumeAI/development-docs` です

## Issue Forms

- `Feature` : 新機能・機能改善・ドキュメント改善
- `Bug` : 不具合修正
- `Experiment` : AI・アルゴリズム・評価実験

## 基本フロー

1. Issue を作成する
2. Issue の目的と完了条件をチーム内で共有する
3. Issue 番号を含む Branch を作成する
4. 実装・検証する
5. 手元の検査を流して PASS の記録の行を書き、Pull Request を作成する（下の「merge の前の検査」）
6. レビュー可能なメンバーがいる場合は Review を受ける。独立したレビュー（Reviewer A・B）を受けたら、Merge の門（`MirumeAI/development-docs` の `02_github_workflow/pull_request_review.md`）の条件を満たしてから merge する。Agent が merge するのは、独立したレビューと Merge の門を通したときだけ（人の判断が要る変更は人の判断の後）。人の確認は、誰が merge しても省かない（同じ文書の「どの Pull Request でも省かないこと」）
7. merge する head で検査が PASS していることを確かめ、原則 Squash and merge する
8. Issue を Close する

### Branch 命名例

- `feature/42-camera-reconnect`
- `fix/51-login-error`
- `experiment/73-patchcore-backbone`

## 適用範囲

この `.github` Repository のテンプレートと `SECURITY.md`・`SUPPORT.md` は、MirumeAI Organization 配下の各 Repository（Private を含む）に同種の独自のファイルがない場合に既定値として利用されます。

## merge の前の検査（`run_required_verification.py`）

GitHub Actions は、Private の Repository では使いません（この Repository だけ動きます）。merge の前提は、**PR で動く workflow の段を、merge する head で手元で流して PASS すること**（Required Verification）です。命令を手で組み立てず、この道具で流します。

```bash
git -C <この Repository の clone> pull --ff-only     # 正本の道具を最新にする
git -C <検査する Repository の clone> fetch origin
python3 <この Repository の clone>/scripts/ci/run_required_verification.py \
    <検査する Repository の clone> --head <PR の head の commit>
```

- 検査する Repository の `.github/workflows/` から、`pull_request` で動く workflow を選びます（`paths`・`branches`・`types` の条件も、`origin/main` との merge-base からの差で見ます。日本語など ASCII でない名前のファイルも、名前のまま照らします。#73）。job と段は CI と同じ順で流します。再利用 workflow と正本の道具は、この clone の `origin/main` から取ります
- job ごとに、新しいものを 3 つ用意して流します
  - head の新しい clone
  - workflow が求める版の Python の新しい venv（`python3.10` などを PATH から探します。無ければ `--python 3.10=<道>` で渡します）
  - 仮の HOME
- 待ち合わせの無い job（`needs` を満たしたもの）は、`--jobs`（既定 3）まで同時に流します。GitHub も別の機械で同時に流すので、1 つずつ流すと変異の表を分けた job の時間が足し算になっていました（#149）。結果は plan の順に出し、job が終わるたびに書き出します。`--jobs 1` なら 1 つずつ流します。job ごとの `concurrency` がある workflow も、1 つずつ流します（GitHub は同じ組の job を同時に流さないため。組は照らしません）。同時に流すときは、計算の部品のスレッドの数（`OMP_NUM_THREADS` など）を「CPU の数 ÷ 同時に流す数」に絞ります（どの job も CPU の数だけスレッドを立てて取り合い、job の時間が上限に近づいたため。workflow に同じ変数を書いてあれば、そちらを使います）。CPU の少ない機械で job が時間の上限を超えるときは、`--jobs 1` で流します。同時に流すのは 1 本の検査の中だけで、ほかのセッションの検査と重ねない取り決めは変えません
- Ctrl-C・SIGTERM で止めると、流している段の process の組を止め、次の段を始めずに、作業の場所（clone・venv・仮の HOME）を消して止まります（終了コード 130・143。PASS の記録の行は出ません。#149）
- 元の clone と HOME は変えません。環境変数は決めた名前（`PATH`・`LANG` など）だけを渡し、token・ssh の agent・git の設定などは渡しません。段が終わったら、後ろで動き続ける子の process も止めます（`setsid` で抜けたものは止められません）。pip で依存を入れるので、network が要ります
- 知らない書き方（action・式・matrix・`defaults`・`pull_request_target`・再利用 workflow の入力の式など）があれば、何も流さずに止まります（終了コード 2）。同じ鍵が 2 回ある workflow（同じ job の id を 2 回書いたもの・`on:` と `"on":` を両方書いたものなど。GitHub は受け付けず、黙って後の値で読むと先の job が消える）、合わせる鍵（`<<`。明示の鍵で中身を黙って上書きできる。node 全体の alias は読む）、明示の YAML 1.1 の型（`!!omap`・`!!pairs`・`!!set`・`!!binary`）、YAML として読めない workflow も同じく止まります。道具の中で思わぬ誤りが起きたときも、traceback でなく 1 行の知らせで止まります（PASS の記録の行は出ません。job を流し始める前なら終了コード 2、流し始めた後なら `結果: FAIL` の行を出して終了コード 1。例外の文にある URL の資格情報は伏せます）。落ちた段があれば FAIL です（終了コード 1）
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

## 門の記録と頼みの文（`gate_record.py`）

Reviewer への頼み・Merge の門の記録・merge の後の記録・Pull Request の本文の記録の行を、検査の記録と git・Pull Request の状態から作ります。40 桁の head や記録の行を、手で写さないためです。**文を作るだけで、GitHub へ書き込みません**（`gh pr view` で読むだけ）。投稿・本文の差し替え・merge は、中身を確かめてから行います。

```bash
G=<この Repository の clone>/scripts/ci/gate_record.py
# 初めの頼み（Reviewer B は --role b と、作成者の「人の判断が要る変更」の判定の --human）
python3 $G review <作業ツリー> --pr 42 --issue 41 --role a --rv-log <検査の出力>
# head が変わった後の確かめ直しの頼み
python3 $G review <作業ツリー> --pr 42 --issue 41 --role a --rv-log <検査の出力> --prev <前の head> --fixed <直したこと>
# 本文の記録の行を、今の検査の記録の行に差し替えた本文
python3 $G body <作業ツリー> --pr 42 --rv-log <検査の出力> --out body.md
# 門の記録と、head を固定した merge の命令
python3 $G gate <作業ツリー> --pr 42 --rv-log <検査の出力> --record record.json --out gate.md
# merge の後のコメント
python3 $G merged <作業ツリー> --pr 42 --head <固定した head> --out merged.md
```

- `<検査の出力>` は、`run_required_verification.py` の出力を書いたファイルです。PASS の記録の行が 1 つだけあり、その head が作業ツリーと Pull Request の head と同じで、基準を含み、道具が正本と同じでなければ止まります
- `review` の `--look`・`--fixed`・`--human` は、人の判断・人の確認の言葉に触れるだけなら書けます。記録の形（印の言葉の後に、記号だけ〔と `の`・`が`・`を`・`'s`・`結果`・`は`〕をはさんで `:`・`=`・ダッシュ・矢印・`|`・かぎ括弧・`✓` などの印か、`PASS`・`承認` などの結果の言葉が来る形）は止まります。`の`・`が`・`を` の後ろの結果の言葉（`確認` も）は、名詞として触れる形（すぐ後に `の` が来る形と、すぐ後に `を` と、結果をまだ得ていないと言う言葉〔`待つ`・`求める`・`頼む`・`お願いする`・`要する`・`要求する`・`要請する`・`依頼する`・`仰ぐ`・`必要とする`。`頼`・`要`・`仰` は活用の字まで見て、`頼りに`・`要約`・`仰せ` は通さない。名詞の `頼み`〔`頼みに`・`頼みとして`・`頼みの綱`〕と `要するに` も、間に空白 1 つをはさむ形も通さない。そのため `頼みに行く`・`頼みにくい`・`要するには` も止まる〔`頼む`・`要する` と言い直せば通る〕〕が来る形と、送り仮名の後に問いの `か`〔後ろが文の終わり・ひらがなでない字・`を`・`は`・`も`・`どうか`〕が来る形。`Human Adjudication が merge の前に要るか`・`Human Verification の完了を待つか`・`Human Verification が完了したか確かめる`）だけを書けます。ほかの形（`Human Verification が完了しています`・`Human Verification が完了したから merge`・`Human Verification の承認を得た`・`Human Adjudication の承認を受けた`・`Human Verification の PASS を見た`・`Human Verification の承認者` など）は記録の形とみなして止まります（言い直せば通る）。`を` の後ろの一覧に無い言葉は、触れるだけの形（`Human Verification の完了を確かめる`）・ひらがなの言い方（`をまつ`）・読点をはさむ形（`を、待つ`）も止まります（`Human Verification が完了したかを確かめる`・`の完了を待つか` と言い直せば通る）。道具は形だけを見るので、すぐ後に `の` が来る断言（`Human Verification が PASS の状態です`）と、一覧の言葉の後ろに続く語で言い切る断言（`Human Adjudication の承認を待ち、受けた`）は通ります。頼む文に、人の判断・人の確認が済んだと書かないことは、Agent が守ります
- `gate` は、文を作る前に次を確かめ、1 つでも外れたら止まります（終了コード 1）。`--record` の JSON の形は、道具の冒頭にあります
  - main を fetch して、検査の基準から進んでいない
  - Reviewer A・B の最後の結果が、merge する head を見て BLOCKER・MAJOR・IN-PR が 0
  - In-PR の残りが無い
  - 作成者と Reviewer A・B の「人の判断が要る変更」の読みが同じ
  - 当たるときは、人の言葉のまま（1 行）が、この Pull Request（`#番号`）と今の head（12 桁以上）だけを名指ししている（番号だけの答え・古い head・別の Pull Request や Repository の判断は引かない。12 桁より短い別の head を並べた言葉も引かない。道具が見分けるのは a〜f を含む 7〜11 桁の並びだけで、数字だけ・6 桁以下の並びは Agent が言葉を読んで確かめる。全角と大文字も照らし（元の文と、全角を半角にそろえた文の両方で探す。ゼロ幅の空白などの書式の字は外し、上に重なる結合の字は外した文と外さない文の両方で照らす）、`PR 7`・`PR-7`・`PR ― 7`（U+2010〜U+2015 のダッシュと長音の `ー` も）・`PR: 7`・`PR = 7`・`PR 番号 7`・`PR ナンバー 7`・`Pull 7`・`7 番の PR`（前に並べた番号も、`番` の有無によらず数える）・`Pull Request 7`・`Pull Request number 7`・`PR number is 7`・`PR #s 7`・`プルリク 7`・URL の `/pull/7` のような番号と、`#7・8`・`#7 と 8`・`#7〜9`・`#7–9`・`#7 & 8`・`#7 or 8`・`#7 to 8`・`#7ー8` のように並べた番号も数える（区切りの後ろの `10/28`・`10:30` のような日付と時刻は数えずに飛ばし、その後ろの番号は数える。年の無い `8-9` は日付と読まない。番号の前置きの直後と、`/`・`-`・`〜` などでつないで続く番号は、日付の形でも数える。URL の番号〔すき間なくつないだ 2 つ目の URL・`/` の前後に空白のある `<owner> / <名前>/pull/7` も。`…/pull/7#issuecomment-…` のようなコメントの位置の付いた URL は、URL の Repository で照らし、位置の後ろに並べた番号も数える〕と、`MirumeAI/<名前>`（`MirumeAI / <名前>`・`MirumeAI の <名前>`・`MirumeAI: <名前>`・`MirumeAI <名前> PR 7` も）・`<owner>/<名前>#7`（`<owner> / <名前> #7`・`<owner>/<名前> の #7`・`<owner>/<名前>: #7`・`<owner>/<名前> で PR 7`・`<owner>/<名前> No.7`・`<owner>/<名前> Pull #7`・`<owner>/<名前> Pull-#7`・`<owner>/<名前> No. #7`・`<owner>/<名前> No.: 7`・`MirumeAI <名前> Pull #7` も）の略した書き方と、owner の無い URL の `<名前>/pull/7` は、その Repository がこの Pull Request の Repository でなければ止まる〔`10/28 #7` のような数字だけの並びは Repository と読まない〕。ほかの Repository の名前は、前が `-` でも名前として照らす〔`a-PDM`。`Image-Acquisition-PDM` の中の `PDM` は除く〕。つなぐ区切りの後ろの日付〔`#7 – 10/28`〕と `No.1` のような項目の番号は、番号の並びと見分けられないので止まる〔言い直せば通る〕。`MirumeAI の main の #7`・`Reviewer A/B の PR #7` のように、Repository の名前と見分けられない言葉の並びと、位置が番号の URL〔`…/pull/7#7`〕・ファイルの位置の URL〔`…/pull/7/files#diff-…`〕も止まる〔言い直せば通る。URL は `…/pull/7` までにする〕）。人の言葉に人の判断の印（`Human Adjudication`。字の間の空白・記号・括弧の有無を問わない）や人の確認（`Human Verification`）の言葉があれば止まる。Agent が head を別に書いて、人が名指ししたことにはしない）
  - 検査の記録の行の飛ばした試験が 0 でなければ、理由を確かめて `--record` の `skipped_reason` に書いてある（門の記録に件数と理由を出す）
- `gate` が出す merge の命令は、head を固定し、main が進んでいたら merge まで進みません
- base が main でない Pull Request（LEGACY の長期 Branch）と、人の判断が Pull Request のコメントの形（`Human Adjudication: MERGE APPROVED` と対象の head）のときは扱いません（決まりの形で手で書きます）
- 人の確認（Human Verification）が要る Pull Request も扱いません。人の確認は、人が自分で Pull Request に書きます。どれが要るかの正本は、development-docs の `03_customer_development/customer_isolation_check.md` の「要る場面」です。`gate` が機械で見分けて止まるのは、そのうち 2 つ（顧客 Repository の `customer-profile.yaml` を変える・検査が `PROFILE EXPANSION DETECTED` を出した）だけです。**`gate` が止まらないことは、人の確認が要らないことの根拠になりません**（ほかの場面に当たるかは Agent が確かめます）。2 つ目を見分けるため、`gate` は検査の出力にある段ごとの記録の場所を読みます。顧客 Repository で段ごとの記録が読めなければ止まります
- 門の記録の最後の文は、道具が確かめた条件だけを言います。道具が見ない条件（development-docs の `02_github_workflow/pull_request_review.md` の「Merge の門」の一覧）と、人の確認が要る場面に当たらないことは、Agent が確かめてから merge します
- 顧客 Repository の文と merge の命令は画面に出さず、`--out` のファイルと、その名前に `.merge` を足したファイル（どちらも本人だけが読める形）に書きます

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
| 2 | 宣言・設定・検査の誤り（宣言が無い・読めない・資格情報らしい鍵がある、設定の JSON やレシピ・機体の設定の YAML が読めない・同じ鍵がある、PyYAML が無い など）。照らせないときは合格にしません |

見るもの:

- 層 1: カメラの IP と製造番号、PLC の宛先と番地、トリガを決まった場所から取り出し、宣言と照らす。補いとして、決まった場所の外の IPv4 と `trig_` で始まる語も照らす（JSON の鍵と値、`.txt` の中、ファイルの名前）
- S1: `config/` の下は、Core が読む決まった名前と、宣言した追加のファイルだけ（説明の `.md` と空の `.gitkeep` は除く）
  - `config/image-acquisition/`: `config.json`・`pdm_guard_policy.json`・`recipe.json`・`machine.json` と、宣言した面の `<面>.json`・`<面>__region*.json`（面は、面の名前か、末尾の `_<数字>` を落とした基の名前）
  - `config/pdm/`: 面ごとの設定の置き場（`adaptive_mask`・`align`・`bbox_acquire`・`good_pool`・`region_split`。ファイルの名前が宣言の面か）と `align_reference.txt`。`defaults/` は宣言しても許しません
- S2: PLC・NAS・AI を使うかが宣言と同じか。PLC を使わないなら、`plc` は `{"enabled": false}` だけ。`nas.enabled` を書いていなければ、照らさずにそう言います（推測しません）
- S3: 構成の名前（`_profile`）・検査の性質（`flow` か `inspection` の一方）・カメラ・面が宣言の中か
- S4: `deploy/` と `versions.yaml` が触れる設定のファイル（`config/...` の道。`./`・`../` で始まるものも含む。それと `config` で始まる `.json` の名前）が、実在して許されているか。開発の機体だけの設定（この Repository の `dev/` の道・`./dev`・前にほかの道がある `dev/config`・`MIRUME_DEV_CONFIG` の名前）を指していないか（名前は、直前が環境から外す形〔`env` コマンドの `-u`・`--unset`（`env` に渡す `-u` だけを並べた行も）、`unset`、`UnsetEnvironment=`、文の始めの値を使わない `<辞書>.pop(`、`del <辞書>[`〕のときのほかは、どこに書いても断る。行を `\` で続けた命令は 1 つにつないで照らす。`deploy/` の説明の `.md` は照らさない）
- S5: 記録（`deployment/`・`validation/`。配置の記録・検証の記録と、その手順と証拠のファイル）。記録には値を書かないので、宣言に無い IPv4 と `trig_` で始まる語は不合格。参考だけにするのは雛形の 3 つの `README.md`（`deployment/`・`validation/`・`validation/protocols/`）の中だけで、手順と証拠の `.md` も照らす。鍵 `version` の値（package の版）は IPv4 とみなさない。空でない `.gitkeep` も照らす。下のリンクは読まずに断る。検証の記録の ID の案件の記号（`validation/VR-<記号>-<年>-<連番>.yaml`）が、記録どうしと、`release/` の System Release の ID の記号と同じか
- S6: 開発の機体だけの設定（`dev/config/`）。置けるのは `dev/config/image-acquisition/<案件の名前>.json` と、説明の `.md` と空の `.gitkeep` だけ。機体へは配らないが、案件の値が入るので、撮像側の設定と同じに照らす（層 1・S2・S3。S2 は、宣言で使うものを切る `enabled: false` を許す。`plc`・`nas`・`ai` の節を書いたら `enabled` を `true` か `false` で書く。無いのも断る）。`dev/config/` の外の `dev/` は照らさない。下のリンクは読まずに断る
  - 前の形で撮像側の主の設定 `config/image-acquisition/config.json` が無く、`config/` に説明（`.md` と空の `.gitkeep`）のほかが無いとき（据え付けの計画が無く、開発の機体だけの設定だけを置く案件。`MIRUME_DEV_CONFIG` で渡すと、開発の機体では主の設定として読まれる）は、`dev/config/image-acquisition/` の設定のそれぞれを主の設定の代わりに照らします。主の設定と同じく、`plc`・`nas`・`ai` の節と、`flow` か `inspection` の一方が無ければ止まります（終了コード 2）。宣言で使うものを開発の機体で切るのは、PLC も含めて許します
  - `config/` に説明のほかがある（据え付けの設定を書きかけた・消し忘れた形。`config` そのものや主の設定・説明がリンクのときも）・代わりにできる設定が無い（決まりの外のファイルとリンクだけ、置き場の途中がリンク）ときは、照らす相手が無いので今どおり止まります（終了コード 2。`config-missing`）。手元の作業ツリーに Git の外（`.gitignore` など）の主の設定があるときも止まります（Required Verification は新しい clone で流すので、Git の外のファイルは無く、そこでは開発の機体だけの設定を照らします。Git の外の値は Git に入らないので、混入の道にはなりません）。宣言の状態の行・値の無い初期形で止まること・`--report-only` の扱いは変わりません
  - 前の形で、根に `config/`・`dev/` の書き違い（大文字小文字。`Config/`・`DEV/` など）に見える置き場があれば、照らす範囲の外に設定を置いた形なので止まります（終了コード 2。`config-missing`）
- 参考（不合格にしない）: `deploy/` の中の値と、説明の `.md` の中の値（記録の置き場では、雛形の 3 つの `README.md` だけ）。強制するかは、既存の案件で報告だけの形で流した結果を見て決めます

新しい形（`recipes/case.yaml` がある顧客 Repository）では、レシピ（`recipes/`）と機体の設定（`machines/`）にも同じ照らしを当てます。形と当て先の正本は、development-docs の `03_customer_development/recipes_and_machines.md`（「混入の検査と宣言」）です。前の形（`recipes/case.yaml` が無い）の照らしは変わりません（`recipes/`・`machines/` は照らしません）。ただし、`recipes/case.yaml` が無いのに `machines/`・`recipes/products/` にファイルがある（説明の `.md` と空の `.gitkeep` を除く）か、`recipes/case.yml`・`recipes/Case.yaml` のような書き違いがあれば、照らせないので止まります（終了コード 2）。今の雛形の `recipes/`（`README.md`・`recipes/capture/` など）だけなら、前の形のまま通ります。

- 前の形の撮像側の設定 `config/image-acquisition/config.json` は無くても止まりません。あれば、移る間として前の形と同じに照らします（両方を照らす）。`recipes/case.yaml` がリンク・ファイルでない・Git の外なら止まります（終了コード 2）
- 層 1: `machines/<記号>.yaml` の `profile` のカメラの IP と製造番号・PLC の宛先（`plc.address`）、`recipes/case.yaml` の `profile` の PLC の番地とトリガ・`shot` のトリガを、撮像側の案件の設定と同じ決まった場所で照らす。補いは `recipes/`・`machines/` のすべてのファイル（`profile` の外の `ports`・`extra_origins` なども。説明の `.md` は参考）
- S1: 置けるのは `recipes/case.yaml`・`recipes/products/<宣言した製品の名前>.yaml`・`machines/<記号>.yaml`（見本 `machines/dev.example.yaml` も）・移る間の `recipes/capture/<名前>.json`・説明の `.md`・空の `.gitkeep`・宣言した追加のファイル（`config_files.extra` に `recipes/`・`machines/` の下の道を書ける。製品のファイルは追加のファイルにしても、宣言した製品の名前でなければ許しません）
- S2・S3: `recipes/case.yaml` の `profile` を前の形の主の設定と同じに照らす（`plc`・`nas`・`ai` の節、`flow` か `inspection`、カメラの役、面。節が無い・`plc.enabled` か `ai.enabled` が真偽でない・`flow` と `inspection` のどちらか一方でないときは終了コード 2。`nas.enabled` が無ければ、照らさずにそう言う）。`case.name` は構成の名前として `project.profiles` と照らす（`case.name` が無ければ 2）。機体の設定の `profile` のカメラの役も照らす
- S4: 前の形と同じ照らしに加えて、`deploy/` と `versions.yaml` が `versions.dev.yaml`・見本 `machines/dev.example.yaml`・Git に無い `machines/<…>.yaml` を指していないか（どれも、前にほかの道があっても。`%h/<…>/machines/<…>.yaml`・`${REPO}/machines/<…>.yaml` も、`machines/` から後ろを Git の物と照らす）。`versions.yaml` は注釈の行（`#` で始まる行）を照らしません（雛形が、書かない先として `versions.dev.yaml` を名指すため）
- 製品の名前（S3-product）: `recipes/products/` のファイルの名前と `product` が、宣言の `products` の中か
- レシピに書かない鍵（R-key）: `recipes/case.yaml` の `profile` の、機体だけの鍵（`camera.<役>.serial_number`・`ip_address`・`device_index`、`plc.address`、`nas.mount_path`、`paths`。`plc.address` と `paths` はそのものを、値の型によらず断る）・開発の機体だけが書ける鍵（`camera.<役>.folder`・`loop`、値が `file` の `sdk`）・組み立て器が組む鍵（`pdm_dir`・`pdm_dir_master`・`paths.work`）。製品のレシピの「レシピに書かない鍵」（根か `capture` の下の `name`・`save_name`・`verified`・`extract_mark`・`updated_at`・`extract_history`・`extract_history_broken`、`extract._meta`・`judge._meta`・`extract.sam.device`・`judge.device`・`judge.threshold`・`judge.threshold_bank_at`・`judge.dino_scale`・`judge.print_fields_geom`・`judge.print_fields_sig`）。値が空でも、鍵があれば断ります
- 照合の型（R-opt）: `judge.opt_level` が `auto` なのに、`judge` に `match`・`window_radius`・`edge_margin`・`method`・`layers`・`top_percent` がある
- 機体の設定（M-）: Git にある `machine.kind` が `dev` の設定（見本を除く。`kind` が `installed`・`shown`・`dev` のどれでもなければ終了コード 2）。`profile` の、機体に書けない鍵（M-key。書けるのは機体だけの鍵と機体で上書きできる鍵〔`camera.<役>.serial_number`・`ip_address`・`device_index`・`rotate`・`exposure_time`・`gain`・`apply_settings`、`plc.address`〔`host`・`port`〕、`nas.mount_path`・`keep_back_days`、`paths.work` を除く `paths` の下、`ai_workers`〕と、`kind: dev` の設定と見本だけは開発の機体だけが書ける鍵〔`camera.<役>.folder`・`loop`、値が `file` の `sdk`〕。`_profile`・`flow`・`plc.enabled` なども断る）。見本の `machine.kind` が `dev` でない（`machine` が無いときも。M-example-kind）。見本 `machines/dev.example.yaml` の中の IPv4（`127.0.0.1` などの一般の値も）・製造番号（`serial_number` の空でない値）・`/path/to/` で始まらない絶対の道（`..` で外へ出る道と Windows の道も）
- 照らす鍵の上の段が対応表でなければ、照らせないので止まります（終了コード 2）: `recipes/case.yaml` の `profile` の `camera` とその役ごとの値・`nas`、機体の設定の `profile` の `camera` とその役ごとの値・`plc`・`plc.address`・`nas`・`paths`
- レシピと機体の設定の YAML は、宣言と同じく別名（`*`）・`<<`・同じ鍵の重複・文字でない鍵を断り、知らない型（`!!binary`・`!!set` など）も断ります（終了コード 2）。数と日付の値は、書いたままの字で照らします（YAML では、引用符の無い先頭が 0 の数が 8 進として読まれるため）
- Git の外の開発の機体の設定は照らしません。製品のパッケージのコード（`src/`・`tests/`・根の `pyproject.toml`）も照らしません（範囲に入れるかは人の判断。決まるまでは Reviewer B が見ます）

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
products: [example_product]      # 新しい形の recipes/products/<製品>.yaml のファイルの名前と product
config_files:
  extra:                         # Core が読む決まった名前の外に置くファイル。理由を書く
    - path: config/image-acquisition/example_extra.json
      reason: 架空の例
extensions: []                   # 差し込む部品の名前（新しい形の entry points。今は数えて増減を出すだけで、照らさない）
```

- 宣言には、その案件の自分の値だけを書きます。資格情報（パスワード・トークン・鍵）は書きません（書くと終了コード 2）
- この検査が見ないもの: 宣言の注釈（`#` の後）は読みません。注釈にも、資格情報とほかの案件の値を書きません（秘密の走査〔`basic_checks.py`〕が探すのは決まった形の鍵だけで、パスワードなどの決まった形でないものと、ほかの案件の値は、どちらの検査も見つけません）。`versions.yaml` は、設定のファイルへの参照（S4）だけを見て、版やタグの値は見ません
- 鍵は文字で書きます。数で書く値（製造番号など）は引用符で囲みます（YAML では先頭の 0 などが消えます）
- `127.` と `255.` で始まる IP、`0.0.0.0`、Core の鍵 `trig_bit`・`trig_ready` は宣言しなくて構いません。文書用の網（`192.0.2.x` など）は除きません
- 値は出力しません。出すのは種類・ファイル・行・欄だけです（決まっていない名前は `<*>` と `<伏せ#n>`）。製品の名前と機体の記号も、ファイルの道の中で伏せます。製品の名前を足す・変える Pull Request は、宣言の値を変える Pull Request として人が値を確かめます
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

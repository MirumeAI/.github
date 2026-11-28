# MirumeAI/.github

MirumeAI Organization の、すべての Repository で共通に使う物の正本です。

> **重要**
> この Repository (`MirumeAI/.github`) は Public で運用します。
> 顧客名、社内URL、IPアドレス、秘密情報、具体的な認証情報などは記載しないでください。

## 目的

MirumeAI では、Core（PDM・Image-Acquisition-PDM）や顧客ごとの Repository など、いくつもの Repository を同じ決まりで開発します。同じであるべき物を各 Repository が別々に持つと、直しが一部にだけ入り、少しずつずれていきます。そこで、どの Repository でも同じであるべき物（雛形・守りの仕組み・検査の道具）をこの 1 か所に置き、ここで直せば全体に行き渡るようにしています。

開発の手順と決まりそのもの（Merge の門など）の正本は、Private の `MirumeAI/development-docs` です。この Repository は、その決まりを支える雛形と道具を持ちます。

## どう届くか

| 届き方 | 何が | どうなる |
|---|---|---|
| GitHub が既定として見せる | Issue と Pull Request の雛形（白紙の Issue は作れない）、脆弱性の報告と困ったときの案内 | 各 Repository は複製せずに使う（同じ種類のファイルを持たないとき） |
| 各 Repository へ写す | Claude Code のガード、共通の command と Issue Form の道具、検査の道具の一部（一覧は `scripts/ci/copy_sync_check.py` の `SHARED`） | 各 Repository では直さない。ここで直し、同期の Pull Request で配る。写しが正本と同じかは、検査が照らす |
| 呼んで・流して使う | 共通の検査（再利用 workflow と検査の道具） | 各 Repository の workflow が呼ぶ。merge の前の検査は、この Repository の clone から手元で流す |
| 新しい Repository の元にする | 顧客案件の Repository の雛形 | 顧客案件の Repository を作るときに写す |

## 構成

| 場所 | 何のためか |
|---|---|
| `.github/` | Issue Form（Feature・Bug・Experiment）・Pull Request の雛形・脆弱性の報告の窓口と、workflow（各 Repository が呼ぶ再利用 workflow と、この Repository の検査） |
| `.claude/` | Claude Code のガード（作業 Branch でない Branch への commit などを止める）・共通の command と Issue Form の道具・この Repository の設定（新しい顧客 Repository の設定の元にもなる） |
| `scripts/ci/` | 共通の検査の道具とその試験。一覧と使い方は [scripts/ci/README.md](scripts/ci/README.md) |
| `templates/customer-project/` | 顧客案件の Repository の雛形。始め方は中の `README.md` |
| `SECURITY.md`・`SUPPORT.md` | 脆弱性と秘密の漏れの報告、困ったときの案内 |
| `CONTRIBUTING.md` | Organization の開発の流れと Branch の名前の要約（手順の正本は development-docs） |
| `CLAUDE.md` | この Repository で作業する Claude Code への指示 |

## 変えるとき

- 写し（一覧は `scripts/ci/copy_sync_check.py` の `SHARED`）は、各 Repository で直しません。この Repository で正本を直し、merge の後に同期します
- 守りに関わる物（ガードとその試験、`.claude/settings.json`、CI の守りの検査とそれを流す workflow など）を変えるときは、merge の前に人の判断が要ります
- 変え方の流れは [CONTRIBUTING.md](CONTRIBUTING.md) と、この Repository の [CLAUDE.md](CLAUDE.md) の「変更時の手順」にあります

## 詳しい文書

- 検査の道具の一覧と使い方（merge の前の検査・門の記録・顧客の間の混入の検査と宣言の書き方・Product Release の識別）: [scripts/ci/README.md](scripts/ci/README.md)
- 開発の流れと Issue・Pull Request の書き方: development-docs の `02_github_workflow/github_rules.md`
- レビュー・Merge の門・人の判断が要る変更の一覧: development-docs の `02_github_workflow/pull_request_review.md`
- ガードと写しの同期: development-docs の `01_getting_started/claude_code_admin.md`（「ガードを変える」「写しを同期する」）
- 顧客別開発と顧客 Repository: development-docs の `03_customer_development/`

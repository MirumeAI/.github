# セキュリティの報告

MirumeAI の Repository で見つけた脆弱性や、秘密情報（token・パスワード・鍵など）の漏れは、Issue・Pull Request・Discussion などの公開の場所に書かないでください。

## 報告のしかた

GitHub の非公開の脆弱性報告（Private vulnerability reporting）で受けます。

1. https://github.com/MirumeAI/.github/security/advisories/new を開きます（この Repository の **Security** タブの Advisories にある **Report a vulnerability** からも開けます）
2. 何が・どこで・どうすると起きるかと、その影響を書いて送ります

報告を読めるのは、報告した人と、この Repository を管理する人（Organization の Owner を含む）だけです。対象がほかの Repository でも、ここから報告してください。

## 書かないでほしいもの

- 秘密の値そのもの（token・パスワード・鍵）。どこに、どの種類の秘密があるかだけを書いてください
- 個人の情報、顧客の名前・設定の値・データ
- 顧客の案件の Repository についての報告では、Repository の名前を書かず「顧客の案件の Repository」と書いてください（どの Repository かは、受けた人が確かめます）

## Organization のメンバーへ

秘密を誤って commit・push したときは、報告の前に、その秘密を無効にして発行し直してください。手順は `MirumeAI/development-docs` の `05_security/secrets_and_data.md` の「認証情報を誤って Commit した場合」です。

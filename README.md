# Discord Bot + PayPay Cog 統合版

添付されたDiscord Botを本体として、先ほどのPayPay処理を `Cogs.paypay` として追加した構成です。

## 構成

```text
.
├── main.py             # 添付されたBot本体
├── paypayu.py          # PayPay通信処理
├── utils.py            # PayPay Cogの権限チェック
├── requirements.txt
└── Cogs/
    ├── __init__.py
    └── paypay.py       # /paypayログイン、/paypayログアウト等
```

本体側にも自販機機能があるため、旧プロジェクトの `Cogs.vending`、`Cogs.setting`、`Cogs.kyash_cog` は読み込まない設定にしています。これにより、自販機コマンドやデータ形式の競合を避けています。

## 起動

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Windowsの場合は次のようにします。

```powershell
py -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
py main.py
```

## 必要な環境変数

最低限、次を設定してください。

```text
DISCORD_BOT_TOKEN=Discord Botトークン
```

添付コードの認証機能も使う場合は、次も設定します。

```text
DISCORD_CLIENT_ID=DiscordアプリケーションID
DISCORD_CLIENT_SECRET=Discord OAuth2 Client Secret
DISCORD_GUILD_ID=対象サーバーID
REDIRECT_URI=https://あなたの公開URL/callback
GOOGLE_CREDENTIALS_JSON=GoogleサービスアカウントJSON
SPREADSHEET_NAME=DiscordVendingDB
```

Renderなどで `RENDER_EXTERNAL_URL` が設定されている場合、リダイレクト先は自動的に `${RENDER_EXTERNAL_URL}/callback` になります。

## PayPay側の使い方

Bot起動後、許可されたユーザーが以下のコマンドを使用できます。

- `/paypayログイン`
- `/paypayログアウト`
- `/paypayプロキシ設定`

`utils.py` の `ALLOWED_USER_IDS` に管理者として許可するDiscordユーザーIDを記入できます。Botオーナーとサーバーオーナーは自動的に許可されます。

## 注意

PayPayの電話番号・パスワードを保存する処理が含まれるため、公開リポジトリへそのまま置かないでください。また、PayPay側の仕様変更や利用規約の影響を受ける可能性があります。利用するアカウントとAPIアクセスが許可された環境で使用してください。

## 「アプリが応答しません」と表示される場合

PayPay通信には時間がかかることがあるため、ログインコマンドは最初に処理中応答を返し、通信完了後に「OTP入力画面を開く」ボタンを表示する方式になっています。電話番号とパスワードを送信したあと、表示されたボタンを押してSMSの4桁コードを入力してください。

通信は20秒でタイムアウトします。タイムアウトした場合は、プロキシを `none` にしていること、Renderのログに接続エラーがないことを確認してください。

## 日本語コマンドと非公開保存

今回の更新で、Botのスラッシュコマンド名を日本語へ統一しました。主なコマンドは `/設定`、`/ヘルプ`、`/有料自販機作成`、`/有料商品追加`、`/ペイペイログイン`、`/キャッシュログイン` などです。

`/設定`、PayPayのログイン情報、Kyashの認証セッションは、`GOOGLE_CREDENTIALS_JSON` と `SPREADSHEET_NAME` が設定されている場合、Googleスプレッドシートへ保存します。保存先のワークシートは `server_config`、`paypay_accounts`、`kyash_accounts` です。Discord上の設定・ログイン・認証結果は非公開応答で表示します。

Googleスプレッドシート保存を有効にするには、サービスアカウントのメールアドレスを対象スプレッドシートへ編集者として共有してください。

## 有料自販機データの永続化

有料自販機の作成情報、商品名、説明、PayPay価格、Kyash価格、設定、クーポン、購入済みリンク情報はGoogleスプレッドシートへ保存されます。保存先のワークシートは `paid_vending_items`、`paid_stock_notifications`、`paid_coupons`、`paid_role_assignments`、`paid_used_paypay_links` です。

有料自販機の在庫本文は現在も `stock_files` フォルダのテキストファイルを使用します。Renderのように再起動でローカルファイルが消える環境では、再起動後も在庫本文まで維持するには、在庫をGoogle Driveやデータベースへ移す追加対応が必要です。

## Discordの「アプリケーションが応答しませんでした」対策

Googleスプレッドシート保存は変更せず、スラッシュコマンドの開始直後にDiscordへ非公開の保留応答を返す方式にしています。その後、読み込み・保存が完了したら `followup` で結果を返します。保存はバックグラウンドで完了扱いにせず、シートへの書き込み完了を待ってから結果を返すため、保存途中の再起動による欠落を防ぎます。

## Cloudflareメール機能

Cloudflare Email RoutingのキャッチオールをEmail Workerへ接続し、WorkerからこのBotの`POST /mail/incoming`へ受信メールを送ります。公式のEmail Workerは`email()`ハンドラーで受信メールのメタデータとRaw MIME本文を取得できるため、Workerテンプレートでは`postal-mime`で本文を解析してからBotへ転送します。

Render側には次の環境変数を設定してください。

```text
MAIL_DOMAIN=vel0x0.xyz
MAIL_INBOX_CHANNEL_ID=通常表示チャンネルのID
MAIL_ARCHIVE_CHANNEL_ID=保管チャンネルのID
MAIL_WEBHOOK_TOKEN=PythonとCloudflare Workerで一致する長いランダム文字列
```

メールアドレスの発行情報はGoogleスプレッドシートの`mail_accounts`シートへ保存されます。`/メール発行`を実行するとランダムなアドレスが発行され、表示削除ボタンを押すとDiscord上の発行表示だけが削除されます。アドレス自体は停止せず、削除後の新着メールは`MAIL_ARCHIVE_CHANNEL_ID`へ送られます。

Cloudflare Worker側のSecretには次を設定し、`PYTHON_MAIL_URL`はRenderの公開URLに置き換えてください。

```text
PYTHON_MAIL_URL=https://あなたのRender公開URL/mail/incoming
MAIL_WEBHOOK_TOKEN=Render側と同じ値
```

`cloudflare/`フォルダにはWorker本体と`postal-mime`の依存定義を入れています。Workerをデプロイした後、Cloudflare Email Routingのキャッチオールのアクションを`discord-mail-router`へ設定してください。メール本文はDiscordのEmbedに表示し、添付ファイル本体は現版では保存せず、ファイル名・種類・サイズの転送にも対応していません。

Workerのデプロイ例は次のとおりです。

```bash
cd cloudflare
npm install
npx wrangler secret put PYTHON_MAIL_URL
npx wrangler secret put MAIL_WEBHOOK_TOKEN
npx wrangler deploy
```

`PYTHON_MAIL_URL`にはRenderの公開HTTPS URLに`/mail/incoming`を付けた値を入力してください。Cloudflare側のキャッチオールの送信先は、デプロイした`discord-mail-router` Workerを選びます。RenderのBotが停止中・チャンネルIDが間違っている・トークンが一致しない場合、Webhookは成功せず、Cloudflare Workerのログで確認できます。

### メールパネル方式

メールアドレスの発行はコマンド返信ではなく、管理者が`/メールパネル設置`で公開パネルを設置し、利用者がパネルの「メールアドレスを発行」ボタンを押して行います。発行されたアドレスは利用者本人だけに見える非公開パネルで表示され、そのパネルの「表示を削除」ボタンからDiscord上の表示を削除できます。

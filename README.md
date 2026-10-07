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
    └── paypay.py       # /ツムツムpaypayログイン、/ツムツムpaypayログアウト等
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

- `/ツムツムpaypayログイン`
- `/ツムツムpaypayログアウト`
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

Cloudflare Email RoutingのキャッチオールをEmail Workerへ接続し、WorkerからこのBotの`POST /mail/incoming`へ受信メールを送ります。公式のEmail Workerは`email()`ハンドラーで受信メールのメタデータとRaw MIME本文を取得できます。ブラウザのコード編集画面でも動くよう、Workerは外部ライブラリを使わずに本文を抽出してBotへ転送します。

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

`cloudflare/`フォルダには、Cloudflareのブラウザ編集画面へそのまま貼り付けられる外部依存なしのWorker本体を入れています。Workerをデプロイした後、Cloudflare Email Routingのキャッチオールのアクションを`discord-mail-router`へ設定してください。メール本文はDiscordのEmbedに表示し、添付ファイル本体は現版では保存しません。

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

### 個人メールパネルの仕様

`/メールパネル設置`で公開パネルを設置し、利用者が「個人メールパネルを開く」を押すと、利用者本人のDMへ固定パネルを送信します。DMパネルには「メールアドレスを発行」「削除」「コピー」のボタンをまとめ、未発行時はメールアドレス欄を`（未発行）`、発行後は現在のアドレスを表示します。Botを再起動してもDMパネルのメッセージは残り、受信メールはDMパネルの下へ順番に送信されます。

### サーバー内固定パネル方式

メールパネルはDMではなく、`/メールパネル設置`を実行したサーバー内チャンネルに固定表示します。利用者が一人で使う前提の場合、同じ1枚のパネルに「メールアドレスを発行」「削除」「コピー」をまとめます。未発行時はアドレス欄を空欄表示、発行後は同じパネルのアドレス欄を更新し、受信メールはそのパネルの下へ送信します。パネルのメッセージ自体は通常のDiscordメッセージなので、Bot再起動後も消えません。

## ポイント招待リンク

ポイント機能の友達招待は、招待コード入力方式ではなくDiscordの招待リンク方式です。

- `/招待リンク` またはポイントパネルの「招待リンクを発行」で、利用者専用のDiscord招待リンクを発行します。
- 招待リンクからサーバーへ参加すると、Botが招待の利用数を検出して招待を自動登録します。
- 招待成立時の報酬は、招待者100ポイント・参加者50ポイントです。
- アカウント作成から14日未満の参加者は、招待者・参加者の双方とも招待報酬の対象外です。
- 同じ参加者への重複付与はありません。
- Botには対象チャンネルの「招待を作成」権限と、サーバー招待一覧を取得できる権限（通常は「サーバー管理」権限）が必要です。

## ツムツム機能の統合

`tsum_bundle/`にツムツム代行機能を配置し、既存のDiscord Botインスタンスへ登録しています。**別のDiscord Botを起動しないため、既存Botの`DISCORD_BOT_TOKEN`だけを使用します。** 既存Botのコマンド、Flask Webサーバー、PayPay/Kyash/メール/ポイント機能はそのまま利用できます。

### 設定

`tsum_bundle/tsum_settings.example.json`を元に設定ファイルを作成します。ローカルでは同じフォルダ内の`tsum_bundle/tsum_settings.json`、RenderではSecret File`/etc/secrets/tsum_settings.json`として配置し、次を設定します。

`allowed_user_ids`にはツムツム管理者のDiscordユーザーIDを配列で指定します。スラッシュコマンドの即時同期先は、既存Botと共通の環境変数`DISCORD_GUILD_ID`で指定します（未設定の場合はグローバル同期となり、反映まで時間がかかることがあります）。`tor_enabled`が`true`の場合は起動時にLinux版Torを使い、不要なら`false`にします。RenderでTorを使うゲーム通信のプロキシURLは`socks5h://127.0.0.1:9050`です。Discord通信を直接接続にする場合、環境変数`TSUM_DISCORD_PROXY=direct`を指定できます。TorのSOCKSポートに`http://127.0.0.1:9050`を設定しないでください。Discord Botトークンはこのファイルへ書かず、既存Botと同じ`DISCORD_BOT_TOKEN`へ設定してください。

Renderでは **New → Web Service → Docker** を選びます。Environment Variablesに既存Bot用の`DISCORD_BOT_TOKEN`等を設定し、ツムツムの設定をSecret File `/etc/secrets/tsum_settings.json`として追加します。`TSUM_BOT_CONFIG=/etc/secrets/tsum_settings.json`を設定し、Health Check Pathは`/healthz`にします。`PORT`はRenderが自動設定し、既存のFlaskサーバーが`0.0.0.0:$PORT`で待ち受けます。

無料プランでもWeb Serviceとして起動できますが、15分間受信アクセスがないとスリープするためBotの常時接続は保証されません。Renderのローカルファイルは再起動・再デプロイで失われることがあり、ツムツムの設定・認証情報・JSONデータは現状Googleスプレッドシートへ自動保存されません。Secret Fileは起動時に設定を渡す用途で、Botが変更してもSecret File自体には書き戻されません。変更・生成データの保持には永続ストレージか別途保存先の実装が必要です。`tsum_bundle/tsum_settings.json`、トークン、LINE認証情報はGitHubへコミットしないでください。

### 統合後の主なコマンド

既存Botのコマンドに加えて、`/ツムツムヘルプ`（一般メンバーも利用可能）、`/ツムツムパネル設置`、`/ツムツム無料代行`、`/ツムツム注文状況`、`/ツムツムログイン確認`、`/ツムツム料金設定`などのツムツムコマンドが同じBotから利用できます。`/ツムツムヘルプ`はツムツム機能のコマンド名と説明を一覧表示します。既存Bot側との完全一致コマンドは確認時点でありません。

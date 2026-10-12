# Discord Bot / AUTOCAT JP — 運用・設定ガイド

このリポジトリは、**Discord Bot、Webアプリ、ゲームデータ処理サイト、メール連携**をひとつのPythonアプリとして動かすプロジェクトです。READMEはコードを変更したときに更新してください。環境変数、設定JSON、各サービスの画面表示はデプロイ先や外部APIの更新によって変わることがあります。

> **秘密情報をREADMEやGitへ書かないでください。** Discord Bot Token、OAuth Client Secret、GoogleサービスアカウントJSON、PayPay/Kyash/LINE認証情報、Cookie、Webhook秘密鍵、プロキシ認証情報は、RenderのEnvironment Variables/Secret Filesなどの秘密保管機能で管理します。

## 目次

- [全体構成](#全体構成)
- [機能一覧](#機能一覧)
- [リポジトリ構成](#リポジトリ構成)
- [必要な外部サービス](#必要な外部サービス)
- [Discord Developer Portalの設定](#discord-developer-portalの設定)
- [環境変数](#環境変数)
- [ツムツム設定ファイル](#ツムツム設定ファイル)
- [Google Sheetsの設定と保存データ](#google-sheetsの設定と保存データ)
- [Cloudflareメール連携](#cloudflareメール連携)
- [ローカル起動](#ローカル起動)
- [Docker / Renderへのデプロイ](#docker--renderへのデプロイ)
- [Discord内での初期設定](#discord内での初期設定)
- [データ永続化・バックアップ](#データ永続化バックアップ)
- [ログとトラブルシューティング](#ログとトラブルシューティング)
- [セキュリティ・運用上の注意](#セキュリティ運用上の注意)

## 全体構成

- `main.py`がDiscord BotとFlask Webサーバーを起動します。
- `main.py`はPayPay、自販機、Kyash、メール、ポイント、AutoCat Discord自販機のCogと`tsum_bundle/`のツムツム機能を**同じDiscord Botインスタンス**に登録します。通常のデプロイではDiscord Bot Tokenをひとつ使います。
- `autocat_main.py`はAutoCatの処理エンジン/APIです。利用者はWeb画面を使わず、Discord内のパネルで注文します。`/autocat/`はDiscordでの利用案内だけを表示します。
- Webサーバーの`/healthz`はRender用ヘルスチェックです。既定の待受ポートは`10000`で、`PORT`環境変数があればそちらを使います。
- DockerイメージにはTorを含み、`start.sh`が既定でTor SOCKS（`127.0.0.1:9050`）とBot/Webプロセスを起動します。
- 依存パッケージは`requirements.txt`に記載し、Python 3.12を基準にしています。

## 機能一覧

### Discord Bot本体（`main.py`）

- サーバー別ロール・ログチャンネル設定
- お問い合わせチケットパネルと認証パネル
- Discord OAuth2によるユーザー認証、認証ユーザーへのメンバーロール付与
- 定期お知らせの設定、確認、テスト送信、解除
- OAuthユーザー情報の手動バックアップと、一括サーバー参加処理
- 管理者によるBot発言、チャンネル再作成
- Discord内で完結するAutoCat注文パネルと、`/healthz`ヘルスチェック
- Cloudflare Workerから受け取る受信メールWebhook

**本体コマンド**

`/設定`、`/チケット設置`、`/認証設置`、`/お知らせ設定`、`/お知らせ確認`、`/お知らせテスト`、`/お知らせ解除`、`/バックアップ`、`/一括呼び戻し`、`/発言`、`/ヘルプ`、`/にゃんこ代行`、`/にゃんこ自販機設置`（管理者）、`/チャンネル再作成`

### AutoCat JP 代行課金

- 共有自販機は`AUTOCAT_PANEL_CHANNEL_ID`にチャンネルIDを一度設定すると、Bot起動時に自動で設置されます。起動・再接続時に直近50件から既存の自販機パネルを探し、見つかれば再利用するため重複しません。パネル用チャンネルにはBotの「チャンネルを見る」「メッセージを送信」「メッセージ履歴を読む」権限が必要です。環境変数を使わず、管理者が`/にゃんこ自販機設置`を一度実行して設置する方法も残しています。
- 個別操作を始めるときは`/にゃんこ代行`でも本人専用パネルを開けます。共有パネルのボタンを使えば、利用者はスラッシュコマンドを入力せず注文できます。各利用者はDiscordアカウントIDで識別され、OAuthやサイト独自アカウント登録は不要です。
- Discord内でサービス（引き継ぎコード代行／新規作成／複製）を選び、**アイテム・ステージ・システム・ガマトト・キャラクター**を最大5カテゴリすべて選択できます。選んだカテゴリごとの項目ドロップダウンは同じ注文パネル内に同時表示されます。5つの選択欄でDiscordの表示行を使い切る場合、「入力・注文へ進む」ボタンのみ別の本人専用ephemeralメッセージに表示します。項目選択にモーダルやカテゴリ切替は使いません。引き継ぎコードと認証番号の入力には注文情報モーダルを使い、結果は注文者本人のDiscord DMへ送ります。
- Discordパネルの基本カタログは固定値の項目が対象です。新規作成・複製の個数は1〜2で指定できます。キャラ個別指定・アイテム数量指定・詳細ステージ/VIP編集などの高度な設定はDiscordパネルでは提供しません。
- 通常利用者は初回3回まで無料（代行・作成・複製を合算、失敗時は回数を返却）。以後は選択項目数×50円です。作成は出力アカウント数分、複製は複製数分を計上します。
- 支払いは共通ポイント（1ポイント=1円）またはPayPay。ポイントは注文者のDiscord IDから予約し、処理成功で確定、失敗時に返却します。PayPayは注文画面のモーダルへリンクを入力し、金額を照合して受領します。失敗時は管理者へ手動返金依頼を送ります。
- VIPは`AUTOCAT_VIP_GUILD_ID`のサーバーで`AUTOCAT_VIP_ROLE_ID`を持つユーザーです。Discord APIで確認し、VIPは無料利用（単価請求なし）となります。支払い確認後に管理者がDiscord上でロールを付与・解除してください。Discord API障害や設定不足時はVIPを認めません。
- 無料回数・PayPay使用済みリンク・返金依頼の永続台帳はGoogle Sheetsに保存し、RenderではSheets設定と固定の識別鍵が必要です。AutoCatの代行会計はサイトアカウントDBに依存しません。

### PayPay（`Cogs/paypay.py`）

- PayPayの登録・ログアウト
- PayPay通信に使うHTTP/HTTPSプロキシの設定
- ログイン時のOTP入力画面

コマンド：`/ペイペイログイン`、`/ペイペイログアウト`、`/ペイペイプロキシ設定`

### 有料自販機（`Cogs/vending.py`）

- 自販機・商品・価格の作成、設置、変更、削除
- 商品追加後のボタンからUTF-8の`.txt`を1回最大10個まとめて選択し、**1ファイル全文を1在庫**として初期登録（複数行も1件として保持、1ファイル最大32KB、必要なら複数回に分けて追加）
- `/有料在庫追加`では複数ファイル追加、1ファイル追加、または従来の1行1個のテキスト入力を選択可能
- PayPay/Kyashの購入処理、在庫の追加・引き出し・確認
- 公開／購入／非公開ログのチャンネル設定と売上確認
- 在庫追加通知、クーポン、購入時のロール付与
- 購入者専用メールチャンネルおよびポイント連携

コマンド：`/有料自販機作成`、`/有料公開ログ設定`、`/有料購入ログ設定`、`/全体売上`、`/有料非公開ログ設定`、`/有料商品追加`、`/有料在庫追加`、`/有料自販機設置`、`/有料在庫引出`、`/有料在庫内容確認`、`/有料商品削除`、`/有料商品情報変更`、`/有料自販機削除`、`/有料自販機パネル更新`、`/有料在庫追加通知設定`、`/有料在庫追加設定解除`、`/有料自販機クーポン作成`、`/有料自販機クーポン削除`、`/有料自販機クーポン一覧`、`/有料自販機ロール設定`

### Kyash（`Cogs/kyash_cog.py`）

Kyashログインと認証コードによる認証を扱います。コマンド：`/キャッシュログイン`、`/キャッシュ認証`

### ポイント・招待（`Cogs/points.py`）

- ポイント残高・履歴・ランキング
- Discord招待リンクによる友達紹介
- 管理者によるポイント付与・減算、ユーザー情報確認

コマンド：`/ポイントパネル設置`、`/ポイント`、`/招待リンク`、`/ポイント履歴`、`/ポイントランキング`、`/ユーザー情報`、`/ポイント付与`、`/ポイント減算`

### メール（`Cogs/mail.py`, `mail_service.py`, `cloudflare/`）

- メールアドレス発行用パネル
- Cloudflare Email RoutingからWorker経由で受信メールをBotへ転送
- パネル発行アドレス、購入者専用の受信先、保管チャンネルへの振り分け
- メールアドレス情報はGoogle Sheetsストア`mail_accounts`を使用

コマンド：`/メールパネル設置`

### ツムツム（`tsum_bundle/`）

ツムツムの注文・無料代行・ログイン確認・料金設定・チケット/実績管理・サーバー貸出を提供します。通常注文・ゲスト作成・サーバー貸出の会計では共有ポイントを1ポイント＝1円で利用でき、決済失敗時は仮確保分を返却します。ゲームAPI/LINEの仕様変更により動作が変わることがあります。Tor経路が有効な場合は、LINEおよびゲーム通信にTor SOCKSを使います。PayPay処理とDiscord Gateway接続は別経路です。

コマンド：`/ツムツムパネル設置`、`/ツムツムパネル再設置`、`/ツムツム料金読込`、`/ツムツム料金設定`、`/ツムツム無料代行`、`/ツムツムチケットカテゴリー`、`/ツムツムチケットチャンネル設定`、`/ツムツム実績チャンネル設定`、`/ツムツム実績カウンター設定`、`/ツムツム実績カウント設定`、`/ツムツム実績カウント`、`/ツムツム注文状況`、`/ツムツムコマンド禁止`、`/ツムツムコマンド許可`、`/ツムツム無料無制限`、`/ツムツムpaypayログイン`、`/ツムツムpaypayotp`、`/ツムツムpaypay状態`、`/ツムツムpaypayログアウト`、`/ツムツム収益`、`/ツムツム収益リセット`、`/ツムツム状態`、`/ツムツムログイン確認`、`/ツムツムサーバー貸し出し`、`/ツムツム貸出パネル設置`、`/ツムツム貸出料金設定`、`/ツムツムヘルプ`、`/ツムツムコマンド一覧`、`/ツムツム自分のサーバー登録`、`/ツムツム貸出ログチャンネル設定`

### AutoCat Discord自販機・管理機能（`Cogs/autocat.py`, `autocat_main.py`）

- Discord View/Modalでサービス・基本項目を選び、コード入力、支払方法選択、結果DMまで行います。
- `/にゃんこ代行`は本人専用パネル、`/にゃんこ自販機設置`（管理者）は共有注文パネルを設置します。
- 引き継ぎ代行・新規作成・複製に対応。初回3回無料、以降1項目50円、共通ポイントまたはPayPay、Discord VIPロール対応。
- 成功したにゃんこ代行はツムツムと同じ緑色Embed形式で公開実績に投稿し、実績番号を`-01`形式で付けます。`/ツムツム実績チャンネル設定`で指定した投稿先と、`/ツムツム実績カウンター設定`のチャンネル名件数表示（最低2桁、`-00`形式）を共用します。
- PayPay返金依頼の管理画面記録とGoogle Sheets会計台帳を維持します。
- 高度なキャラ個別指定・数量指定・詳細ステージ編集は利用者向け注文から外しています。

`/autocat/`はDiscordで注文する案内画面です。PayPay返金記録等の管理者専用ページは残しています。ゲーム処理APIは`autocat_main.py`にあります。

## リポジトリ構成

```text
.
├── main.py                     # Discord Bot + 公開Flaskアプリ
├── autocat_main.py             # AUTOCATゲーム処理/API/管理機能
├── Cogs/autocat.py             # Discord内AutoCat注文・決済パネル
├── requirements.txt            # Python依存関係
├── Dockerfile                  # Python 3.12 + Torを含むコンテナ
├── start.sh                    # TorとBot/Webの起動・停止
├── persistent_store.py         # Google Sheets + JSONフォールバックストア
├── interaction_guard.py        # Discord Interactionの応答保護
├── Cogs/                       # PayPay、自販機、Kyash、メール、ポイント、AutoCat
├── tsum_bundle/                # ツムツム一式と設定テンプレート
├── ACCOUNT/                    # Webサイトアカウント・VIP・購入チケット
├── CHAT/                       # 公開チャット・DM・管理・DB
├── DISCORD/                    # 管理チャット転送・利用ログ
├── Paython/                    # ゲームデータ処理・WAF関連コード
├── HTML/                       # AUTOCATのWeb画面テンプレート
├── static/                     # ブラウザ用JS/CSS/アセット
└── cloudflare/                 # 受信メール用Email Worker
```

## 必要な外部サービス

| 用途 | 必要なもの | 必須度 |
|---|---|---|
| Discord Bot | Discordアプリ、Bot Token、Botを招待するサーバー | Bot起動に必須 |
| Discord Web認証 | Discord OAuth2 Application ID/Secret、登録したRedirect URI | OAuth認証を使う場合必須 |
| Google Sheets | Google Cloudで有効化したSheets/Drive API、サービスアカウント、共有先スプレッドシート | BotのSheets永続化を使う場合必須 |
| ホスティング | DockerをビルドできるWebサービス（この構成ではRenderのDocker Web Serviceを想定） | 本番稼働に必要 |
| Tor | Dockerfileに含まれるTor | ツムツム設定でTorを使う場合 |
| Cloudflare Email Routing | ドメインのEmail RoutingとEmail Worker | 受信メール機能を使う場合のみ |
| Web Push | VAPID公開鍵・秘密鍵 | チャット等のPush通知を使う場合のみ |

## Discord Developer Portalの設定

1. Discord Developer PortalでApplicationを作成し、Botを作成します。
2. Bot Tokenを発行し、秘密情報として`DISCORD_BOT_TOKEN`に登録します。Tokenをソース、README、Discordメッセージに貼らないでください。
3. **Server Members Intent**と**Message Content Intent**を有効にします。Botは`discord.Intents.default()`にこれらを設定しています。
4. Botをサーバーへ招待し、スラッシュコマンド登録に必要な`applications.commands`スコープを含めます。
5. Botに必要な権限を付けます。機能によって、チャンネル表示/送信、Embed、ロール管理（Botロールを対象ロールより上位にする）、チャンネル管理、招待作成/管理、メンバー取得などが必要です。運用では必要な機能に絞って権限を付与してください。
6. OAuthはDiscord Bot本体の認証機能で使う場合だけ設定します。AutoCat注文・VIPロール判定はDiscord内で行うため、AutoCat利用者向けOAuth設定は不要です。

## 環境変数

以下の値はRenderの**Environment Variables**またはSecret Filesから設定します。ローカルではシェル環境に読み込ませます。トークンや鍵の実値をREADMEへ記録しないでください。

### Bot本体・Webサーバー

| 変数 | 必須 | 既定値 / 説明 |
|---|---:|---|
| `DISCORD_BOT_TOKEN` | はい | 起動する共有Discord BotのToken。秘密値。|
| `DISCORD_GUILD_ID` | 推奨 | コマンド同期先のサーバーID。`0`/未設定ならグローバル同期になり、反映が遅れる場合があります。AUTOCATのVIPサーバーID既定値にも使われます。|
| `DISCORD_CLIENT_ID` | OAuth利用時 | Discord Application ID。|
| `DISCORD_CLIENT_SECRET` | OAuth利用時 | Discord OAuth2 Client Secret。秘密値。|
| `RENDER_EXTERNAL_URL` | Renderでは自動 | Renderが設定する公開URL。本体の`/callback`等を組み立てます。|
| `REDIRECT_URI` | Render以外の本体OAuth | `RENDER_EXTERNAL_URL`がないときの本体Bot認証コールバック。通常は`https://<公開ホスト>/callback`。ローカル既定値は`http://localhost:8080/callback`。|
| `DISCORD_REDIRECT_URI` | 旧Web機能のみ | 旧AutoCat OAuth画面を使用する場合のコールバック。Discord自販機注文には不要です。|
| `PORT` | 任意 | Flaskの待受ポート。既定`10000`。Renderが自動設定します。|
| `SPREADSHEET_NAME` | 任意 | Googleスプレッドシート名。既定`DiscordVendingDB`。|
| `GOOGLE_CREDENTIALS_JSON` | Sheets利用時 | サービスアカウントJSON**全体**を文字列で設定。秘密値。対象シートをサービスアカウントのメールアドレスへ共有します。|
| `MEMBER_ROLE_ID` | 任意 | 本体のメンバーロールIDの初期値。Discord内の`/設定`で変更できます。既定`0`。|
| `STAFF_ROLE_ID` | 任意 | スタッフロールIDの初期値。既定`0`。|
| `ADMIN_ROLE_ID` | 任意 | 管理者ロールIDの初期値。既定`0`。|
| `LOG_CHANNEL_ID` | 任意 | 実績・ログチャンネルIDの初期値。既定`0`。|
| `RUNTIME_DATA_DIR` | 推奨 | AUTOCATのSQLite DB・バックアップ・本体の定期告知設定などの保存先。既定`/tmp/discord-bot-data`。永続ディスクを使う場合はマウント先を設定します。|

### AutoCat Discord自販機・処理エンジン

| 変数 | 必須 | 既定値 / 説明 |
|---|---:|---|
| `PUBLIC_BASE_URL` | 旧Web機能のみ | 旧AUTOCAT Web画面の公開URL。Discord自販機注文には不要です。|
| `AUTOCAT_VIP_GUILD_ID` | AutoCat VIP利用時に必須 | VIPロールを付与するDiscordサーバーID。未設定なら`VIP_GUILD_ID`、次に`DISCORD_GUILD_ID`を参照。|
| `AUTOCAT_VIP_ROLE_ID` | AutoCat VIP利用時に必須 | 有料VIP利用者へ付与するDiscordロールID。Botが対象サーバーに参加している必要があります。ユーザーがこのロールを持つかDiscord APIで確認し、API障害時はVIP権限を付与しません。|
| `AUTOCAT_ITEM_PRICE_YEN` | 任意 | 3回の無料利用後の1項目あたり単価（円）。既定50円、設定範囲1〜100,000円。|
| `AUTOCAT_PANEL_CHANNEL_ID` | 自動設置を使う場合 | 共有自販機パネルを自動設置するDiscordチャンネルID。設定するとBot起動時に設置または既存パネルを再利用します。未設定なら管理者が`/にゃんこ自販機設置`を一度実行してください。|
| `VIP_PLAN_LABEL` / `VIP_PRICE_*` / `VIP_PAYMENT_GUIDE` | 旧Web機能のみ | 旧Web VIPページ用。Discord自販機では管理者がVIPロールを付与します。|
| `FLASK_SECRET_KEY` | **本番では必須** | Cookieセッション・内部暗号化に使うランダムで固定の秘密鍵。再起動をまたいで同じ値を使います。未設定時は互換用フォールバックがありますが、本番では明示してください。|
| `SESSION_COOKIE_SECURE` | 任意 | `1`（既定）ならSecure Cookieを有効化。HTTPSを使わないローカル開発のみ`0`。|
| `IDENTITY_HASH_KEY` | 推奨 | 無料利用者識別・招待等の安定したHMAC鍵。未設定なら`FLASK_SECRET_KEY`を使用。固定値を保つこと。|
| `AUTOCAT_BILLING_SHEET` | 任意 | 課金台帳のGoogle Sheetsワークシート接頭辞。既定`autocat_billing`。`<値>_meta`、`<値>_a`、`<値>_b`の3シートを作成します。|
| `AUTOCAT_PAYPAY_ACCOUNT_ID` | 任意 | AUTOCAT料金を受け取るPayPay口座のDiscordユーザーID。未設定なら`ADMIN_USER`のPayPay登録口座を使います。口座は既存の`/ペイペイログイン`で登録します。|
| `CHAT_IDENTITY_HASH_KEY` | 任意 | チャット匿名ID用の専用鍵。未設定なら`IDENTITY_HASH_KEY`または`FLASK_SECRET_KEY`。|
| `ACCOUNT_IDENTITY_HASH_KEY` | 任意 | 既存サイトアカウント機能の識別用専用鍵。AutoCat代行の利用者識別・ポイント・VIP判定には使用しません。|
| `ADMIN_USER` | 管理機能利用時 | 管理者のDiscordユーザーID（数字）。管理チャット管理者にも使用。旧名`Admin_USER`は互換用。|
| `ADMIN_SNAPSHOT_KEY` | 管理スナップショット利用時 | 管理バックアップ/スナップショット暗号化用の秘密鍵。未設定なら`FLASK_SECRET_KEY`から導出。|
| `DISABLE_BACKGROUND_UPDATER` | 任意 | `1`で起動時のバックグラウンドWebデータ更新スレッドを無効化。既定では有効。|
| `BCSFE_CONNECT_TIMEOUT` | 任意 | BCSFE接続タイムアウト秒。既定`15`、許容`5–60`。|
| `BCSFE_API_TIMEOUT` | 任意 | 通常ゲームAPI応答待ち秒。既定`45`、許容`15–120`。|
| `BCSFE_UPLOAD_TIMEOUT` | 任意 | セーブ等のアップロード待ち秒。既定`90`、許容`30–300`。|
| `PROXY_URL` | 任意 | AUTOCATからのHTTP/HTTPS外向き通信に使うプロキシURL。空ならプロキシなし。認証情報を含むURLは秘密値。|
| `PROXY_CHECK_ON_STARTUP` | 任意 | `1`かつ`PROXY_URL`設定時に外部IP確認を起動時に実行。診断目的以外は不要。|

### メール・チャット・通知連携

| 変数 | 必須 | 既定値 / 説明 |
|---|---:|---|
| `MAIL_DOMAIN` | メール利用時 | 発行するメールアドレスのドメイン。既定値はコード内の`vel0x0.xyz`。利用する受信ドメインに合わせる。|
| `MAIL_INBOX_CHANNEL_ID` | メール利用時 | 通常の受信メールを表示するDiscordチャンネルID。発行パネルごとの設定があればそちらが優先されます。|
| `MAIL_ARCHIVE_CHANNEL_ID` | メール利用時 | アドレス表示削除後などの保管先チャンネルID。|
| `MAIL_WEBHOOK_TOKEN` | Worker利用時 | Render側の`POST /mail/incoming`を認証する長いランダム文字列。Worker側Secretと完全一致させる。|
| `CHAT_ADMIN_DISCORD_IDS` | チャット管理時 | 管理者DiscordユーザーIDをカンマ区切りで指定。`ADMIN_USER`も管理者として加算。|
| `CHAT_DISCORD_BOT_TOKEN` | 任意 | 管理チャット投稿を転送する専用Bot Token。秘密値。未設定ならDiscord転送は無効。|
| `CHAT_DISCORD_CHANNEL_ID` | 任意 | 管理チャット転送先チャンネルID。専用Botに投稿権限を付与。|
| `DISCORD_LOG_BOT_TOKEN` | 任意 | AUTOCAT利用状況ログを送る専用Bot Token。秘密値。|
| `DISCORD_LOG_CHANNEL_ID` | 任意 | 利用状況ログチャンネルID。|
| `CHAT_VAPID_PUBLIC_KEY` | Web Push利用時 | Web Push用VAPID公開鍵。|
| `CHAT_VAPID_PRIVATE_KEY` | Web Push利用時 | Web Push用VAPID秘密鍵。公開しない。生成は`python CHAT/generate_vapid_keys.py`。|
| `CHAT_VAPID_SUBJECT` | 任意 | VAPID連絡先。既定`mailto:admin@autocat.jp`。|

### ツムツム・PayPayプロキシ・Tor

| 変数 | 必須 | 既定値 / 説明 |
|---|---:|---|
| `TSUM_BOT_CONFIG` | 推奨 | Tsum設定JSONのパス。RenderではSecret File（例`/etc/secrets/tsum_settings.json`）。起動時にアプリ書込可能な`tsum_bundle/tsum_settings.json`へコピーされます。|
| `TSUM_TOR_START` | 任意 | `start.sh`がTorを起動するか。`1`（既定）で起動、`0`で起動しない。`0`なら別のSOCKSサービスが必要。|
| `TSUM_PROXY_URL` | 任意 | TsumのLINE/ゲームAPI用プロキシを環境変数で上書き。空ならJSON設定の`tor_enabled`/`tor_socks_url`を使用。Tor用にはSOCKS形式を指定。|
| `TSUM_DISCORD_PROXY` | 任意 | Discord REST/Gateway用のHTTP(S)プロキシ上書き。既定は直接接続。`direct`/`none`/`off`/`disabled`で直接接続を明示。Torの9050番SOCKSポートをHTTPプロキシとして指定しない。|
| `PAYPAY_PROXY_URL` | 任意 | Tsum側PayPay通信のプロキシ上書き。`tsum_settings.json`内の`paypay_proxy_url`より環境変数が優先される場合があります。秘密入りURLは非公開。|

### Cloudflare Worker側だけに設定する値

`PYTHON_MAIL_URL`は**Cloudflare Worker Secret/Variable**に設定し、Renderの公開URLに`/mail/incoming`を付けたURLを指定します。`MAIL_WEBHOOK_TOKEN`もRenderとWorkerの両方に、同じランダム値を設定します。

## ツムツム設定ファイル

テンプレートは[`tsum_bundle/tsum_settings.example.json`](tsum_bundle/tsum_settings.example.json)です。コピーしてローカルの`tsum_bundle/tsum_settings.json`を作るか、Render Secret Fileで同じ内容を渡します。`TSUM_BOT_CONFIG`未設定かつランタイム設定ファイルが存在しない場合、起動時にテンプレートがコピーされます。JSON内にDiscord Bot Tokenを入れてはいけません（古い`token`キーはロード時に削除されます）。

| JSONキー | 既定値 | 説明 |
|---|---|---|
| `guild_id` | `0` | Tsum用ギルドID。コマンド同期は共有Botの`DISCORD_GUILD_ID`が基準。|
| `allowed_user_ids` | `[]` | Tsum管理コマンドを実行できるDiscordユーザーIDの配列。未設定時はコード上のBotオーナー/管理権限チェックも参照。|
| `ticket_category_id` | `""` | Tsum注文チケット作成先カテゴリID。|
| `ticket_channel_id` | `""` | チケット/注文の既定チャンネル設定。|
| `public_result_channel_id` | `""` | 結果通知用チャンネルID。|
| `achievement_channel_id` | `""` | 実績投稿先チャンネルID。|
| `achievement_channel_base` | `""` | 実績カウンターのチャンネル名ベース。|
| `menu_prices` | キー別初期値はテンプレート参照 | 注文メニュー別価格辞書。`coin_max`、`score_max`、`level_max`、`tsum_lv`、`box`、`coin`、`score`、`premium`、`guest_create`を含む。|
| `select_box_id` | `12007013` | 選択ボックスID。|
| `premium_gacha_id` | `12007007` | プレミアムガチャID。|
| `box_fallback_premium_box` | `false` | 指定ボックスが使えない場合のプレミアムボックス代替処理。|
| `premium_fill_coins` | `160000000` | プレミアム系処理で使う補充コイン設定。|
| `free_unlimited_enabled` | `true` | 無料代行の無制限対象機能を有効化。|
| `free_unlimited_ids` | `[]` | 無料制限の解除対象ユーザーID一覧。|
| `free_payment_user_id` | `0` | 無料処理等で参照するユーザーID設定。|
| `sales_file` | `"tsum_sales.json"` | 売上データファイル名。|
| `free_used_file` | `"tsum_free_used.json"` | 無料利用記録ファイル名。|
| `rental_enabled` | `true` | Bot貸出機能を有効化。|
| `rental_file` | `"tsum_rentals.json"` | 貸出状態ファイル名。|
| `rental_prices` | テンプレート参照 | 月数キー（`"1"`,`"3"`,`"6"`,`"12"`,`"0"`）ごとの貸出価格。|
| `rental_log_channel_id` | `""` | 貸出購入・期限切れ等のログチャンネルID。|
| `rental_guild_lock` | `true` | 貸出先ギルドを制限する設定。|
| `rental_leave_unlicensed` | `false` | ライセンス外ギルドへのBot離脱処理。|
| `rental_home_guild_ids` | `[]` | 貸出対象外などにする自分のサーバーID一覧。|
| `guild_settings` | `{}` | サーバー別の設定辞書。|
| `paypay_token_file` | `"paypay_token.json"` | 共有PayPay認証ファイル名。|
| `paypay_token_dir` | `"paypay_tokens"` | サーバー別PayPay認証ファイルのディレクトリ。|
| `paypay_fallback_shared` | `false` | サーバー別PayPay認証がない場合に共有認証へフォールバック。|
| `paypay_proxy_url` | `""` | PayPay用プロキシURL。Tor用Tsum APIプロキシとは別設定。|
| `python_realip` | `""` | PayPay補助プロセス等で使うPython実行ファイル。未設定なら起動中のPython。|
| `tor_enabled` | `true` | Tsum向けTor利用の有効/無効。|
| `tor_socks_url` | `"socks5h://127.0.0.1:9050"` | Tor/他SOCKSプロキシURL。ドメイン解決もプロキシ側で行うため、Torでは`socks5h://`形式を推奨。|
| `tor_game_api` | `true` | ゲームAPI通信をTorプロキシ経由で行う設定。|
| `tor_geoip_file` | 未設定 | Tor GeoIPファイルを標準外の場所に置く場合のパス。見つからない場合、国重複回避機能が無効になることがあります。|
| `discord_proxy_url` | `""` | Discord通信向けのHTTP(S)プロキシ。Tor SOCKSとは別方式・別用途。|
| `panel_messages` | `{"menu": []}` | 作成済みパネルメッセージなどの記録。|

> 設定ファイルの既定値はコード内テンプレートが正です。起動時に実際のTor出口を確認するログは`[tor-check]`で始まります。アプリから設定JSONを書き換えた値をRender Secret Fileへ書き戻す仕様ではありません。ランタイム設定を変更する場合はSecret File側を更新して再デプロイしてください。

## Google Sheetsの設定と保存データ

1. Google CloudプロジェクトでGoogle Sheets APIとGoogle Drive APIを有効にします。
2. サービスアカウントを作成し、鍵JSONを取得します。
3. 対象スプレッドシートをサービスアカウントのメールアドレスへ編集者として共有します。
4. JSON全文を`GOOGLE_CREDENTIALS_JSON`、ファイル名（シート名）を`SPREADSHEET_NAME`へ設定します。
5. 一部のワークシートは起動/保存時に自動作成されます。既存データの移行時は事前バックアップを取ってください。

主なワークシート名：`oauth_users`、`server_config`、`paid_vending_items`、`paid_stock_contents`、`paid_stock_notifications`、`paid_coupons`、`paid_role_assignments`、`paid_used_paypay_links`、`paypay_accounts`、`kyash_accounts`、`mail_accounts`、`points_and_referrals`。ストアはJSON文字列をセルA1へ保存し、ローカルJSONをフォールバックとして使います。

### AUTOCAT利用料金

- **初回の3回の成功した代行実行**は無料です。失敗した処理は回数を消費せず、作成・複製を複数個まとめて実行した場合も、初回無料枠では1ジョブとして数えます。
- 無料枠を使い切った後は、選択した項目1つにつき50円です。新規作成は選択項目数×作成数、複製は複製数で計算します。例：3項目を1回実行する場合は150円。
- 支払いは**共通ポイント（1ポイント＝1円）**または**PayPayリンク**です。ポイント利用には、サイトアカウントとDiscordアカウントをマイアカウント画面で連携してください。PayPay利用時はサイトアカウントへのログインが必要です。
- 有料VIPを管理者がサイトアカウントへ付与すると、契約中は代行・作成・複製が無料・無制限です。招待報酬、週次無料枠、VIP無料体験は停止しています。
- 代行失敗時は予約ポイントを返却します。PayPay受領後に処理が失敗した場合は自動送金返金せず、管理者へDiscord DMで手動返金依頼を送り、管理画面 `/autocat/admin/panel` から返金済みまたは取消を記録します。
- 本番のRender環境では課金台帳の永続化に**Google Sheets**と固定の`IDENTITY_HASH_KEY`または`FLASK_SECRET_KEY`が必要です。デプロイ後、同じスプレッドシートに`autocat_billing_meta`、`autocat_billing_a`、`autocat_billing_b`（または`AUTOCAT_BILLING_SHEET`で指定した接頭辞のタブ）が作られます。Sheets設定または固定鍵がない場合は課金処理を安全側で停止します。
- 課金台帳はサイトアカウントID、ユーザー名、Discord ID、返金額・理由、PayPayリンクのハッシュを記録します。PayPayリンク本文やPayPayパスワードは課金台帳に保存しません。シートの共有権限を必要最小限にしてください。

`GOOGLE_CREDENTIALS_JSON`がない場合、`persistent_store.py`経由の機能は主にローカルJSONへフォールバックします。ただし、OAuth認証ユーザーの`oauth_users`データはSheetsクライアントがないと永続保存できません。認証、売上、在庫、購入者情報などを継続利用する本番環境ではGoogle Sheets設定を行ってください。

## Cloudflareメール連携

1. ドメインのEmail Routingを有効化し、キャッチオールを作成します。
2. `cloudflare/discord-mail-router.js`をEmail Workerとしてデプロイします（Worker内に外部npm依存はありません）。
3. Workerに`PYTHON_MAIL_URL=https://<公開ホスト>/mail/incoming`と`MAIL_WEBHOOK_TOKEN`を設定します。秘密トークンはRender環境の`MAIL_WEBHOOK_TOKEN`と同じ値にします。
4. Email Routingのキャッチオール転送先としてこのWorkerを指定します。
5. Renderには`MAIL_DOMAIN`、`MAIL_INBOX_CHANNEL_ID`、`MAIL_ARCHIVE_CHANNEL_ID`を設定し、Botに対象チャンネルへの閲覧・送信権限を与えます。

任意のWrangler CLIデプロイ例：

```bash
cd cloudflare
npm install
npx wrangler secret put PYTHON_MAIL_URL
npx wrangler secret put MAIL_WEBHOOK_TOKEN
npx wrangler deploy
```

Worker管理画面からデプロイする場合も、Webhook URLと同じトークンを設定してください。添付ファイル本体の保管には現状対応していません。受信メールの表示先チャンネルに適切な閲覧制限を付けてください。

## ローカル起動

### 1. 前提と環境ファイル

- Python 3.12
- Git
- Google Sheetsを使う場合はサービスアカウントJSONと共有済みのスプレッドシート
- Torを使う場合はローカルTorを稼働させて`127.0.0.1:9050`を開く（`start.sh`経由でない`python main.py`の起動はTorを自動起動しません）

仮想環境とパッケージ：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`.env`はGit管理外です。**main.pyを直接起動する場合は、`python main.py`より前にシェルへ環境変数を読み込ませてください。**

```bash
set -a
source .env
set +a
python main.py
```

Windows PowerShellでは`$env:NAME = "value"`で必要な値を設定するか、対応する環境変数読み込み手順を用意します。TokenやSecretをコマンド履歴に残さないよう注意してください。

### 2. Tsum設定

```bash
cp tsum_bundle/tsum_settings.example.json tsum_bundle/tsum_settings.json
```

`allowed_user_ids`、ギルド/チャンネルID、価格等を編集します。`tsum_bundle/tsum_settings.json`とLINE/PayPayセッションファイルは`.gitignore`対象です。ローカルでTorを使う場合、別途Torサービスを起動してからBotを開始してください。

### 3. 起動

```bash
python -u main.py
```

Web画面は`http://127.0.0.1:10000/`、AUTOCATは`http://127.0.0.1:10000/autocat/`です。OAuthをローカルで試す場合はDiscord Developer PortalにもローカルCallback URLを登録します。

## Docker / Renderへのデプロイ

### Dockerで起動

DockerfileはPython 3.12、OS依存ライブラリ、Torを導入し、非rootユーザーで`start.sh`を実行します。`.env`とTsum設定JSONを用意してから実行します。

```bash
docker build -t discord-autocat .
docker run --rm --env-file .env -p 10000:10000 \
  -v "$PWD/tsum_bundle/tsum_settings.json:/etc/secrets/tsum_settings.json:ro" \
  -e TSUM_BOT_CONFIG=/etc/secrets/tsum_settings.json \
  discord-autocat
```

Tsum設定ファイルが不要な環境ではSecret Fileの`-v`と`TSUM_BOT_CONFIG`を省略できます。`start.sh`は既定でTorを起動します。Torを使わない場合は`TSUM_TOR_START=0`を明示し、`tsum_settings.json`でも`tor_enabled`を`false`にしてください。

### Render Web Service

1. GitHubリポジトリをRenderへ接続し、**Docker Web Service**としてデプロイします（Dockerfile/`start.sh`を使用）。
2. Environment VariablesにBot Token、Google Sheets、固定の識別鍵、管理者ID、AutoCat VIPロールID等を設定します。本体OAuthを使う場合のみOAuth情報も設定します。
3. `tsum_bundle/tsum_settings.example.json`を元に本番用JSONを作り、Secret File（例`/etc/secrets/tsum_settings.json`）として登録します。`TSUM_BOT_CONFIG=/etc/secrets/tsum_settings.json`を環境変数に設定します。
4. Health Check Pathを`/healthz`にします。Renderが`PORT`を設定し、アプリは`0.0.0.0:$PORT`で待ち受けます。
5. 本体OAuth認証を使う場合は公開URLの`/callback`をDiscord Developer Portalへ登録します。AutoCatのDiscord内注文にOAuth Callbackは不要です。
6. 初回起動後はRender LogsでCog読み込み、Tor経路確認、Webhook/Sheets接続エラーを確認します。

**永続ディスクと一時ファイル：** `RUNTIME_DATA_DIR`配下にはAUTOCATのSQLite DB、管理バックアップ、本体の告知設定などが置かれます。これらを再起動後も保持したい場合は、ホスティングサービスの永続ディスクをマウントし、そのマウント先を`RUNTIME_DATA_DIR`に設定します。ツムツムの一部セッション/JSONファイルとローカルJSONフォールバックは`/app`配下にも作成され、`RUNTIME_DATA_DIR`だけでは移動されません。永続化が必要なデータのバックアップを別途設計してください。

## Discord内での初期設定

1. Botをサーバーへ招待し、Intent、権限、ロール階層を確認します。
2. `/設定`でメンバー・スタッフ・管理者ロールとログチャンネルを登録します（管理者権限が必要）。
3. `/チケット設置`と`/認証設置`で必要なパネルを設置します。
4. 必要に応じ`/ポイントパネル設置`、`/メールパネル設置`、`/有料自販機作成`、`/ツムツムパネル設置`を実行します。
5. `/にゃんこ自販機設置`でAutoCatの共有注文パネルを設置します。`AUTOCAT_VIP_GUILD_ID`と`AUTOCAT_VIP_ROLE_ID`を設定し、VIPは管理者がDiscord内でロールを付与します。
6. `/ヘルプ`、`/ツムツムヘルプ`で利用者向けのコマンド案内を確認します。

本体の管理コマンドはDiscord管理者権限または`/設定`で登録した管理者ロールを確認します。Cogごとに`utils.py`等の追加チェックがあります。ツムツムは`allowed_user_ids`、Botオーナー/管理権限などのチェックがあり、`/ツムツムヘルプ`は公開コマンドです。

## データ永続化・バックアップ

| データ | 主な保存先 | 注意点 |
|---|---|---|
| Discord OAuth認証ユーザー | Google Sheets `oauth_users` | Token/Refresh Tokenを含む機微情報。閲覧権限を厳格に管理。Sheets設定がないと永続保存できません。|
| Botサーバー設定、自販機、在庫本文、決済アカウント、ポイント/紹介、メール設定 | Google Sheetsの各Store + ローカルJSONフォールバック | Sheets側の値を優先して読み込みます。Sheetsとローカル双方のバックアップを推奨。|
| AUTOCAT課金台帳（初回無料、PayPay重複防止、返金依頼） | Google Sheetsの`autocat_billing_*`行スナップショット + `RUNTIME_DATA_DIR`配下のSQLiteキャッシュ | RenderではSheetsと固定識別鍵が必須。初回無料回数はDiscord ID単位。サービスアカウントには対象スプレッドシートの編集権限が必要です。|
| AUTOCAT利用回数、チャット、招待、操作復旧情報 | `RUNTIME_DATA_DIR`配下のSQLite/JSON | 現時点では永続ディスクがないと再デプロイ/インスタンス交換で失われる可能性があります。|
| 本体の告知設定・手動バックアップJSON | `RUNTIME_DATA_DIR`配下 | `/バックアップ`はOAuth認証データをローカルへ出力。保存先は一時ディスクになり得ます。|
| Tsum設定・LINE認証・セッション・売上・貸出・実行中ジョブ | `tsum_bundle/`配下のJSON等 | 認証情報を含みます。Gitへコミットしないでください。Render Secret Fileは起動時設定の受け渡し用で、アプリの変更を元Secret Fileへ書き戻しません。|

RenderのFree Web Serviceはローカルファイルが再デプロイ・再起動・スピンダウンで消えるため、SQLiteの永続性が必要なデータは外部ストアへ保存してください。AUTOCATの代行利用はサイト独自アカウントDBに依存しません。Discord OAuthで利用者を識別し、VIPロールはDiscord APIで照合、初回無料回数・PayPay重複防止・返金依頼はGoogle Sheets台帳へ保存します。チャット・既存のサイトアカウント機能・操作復旧情報など別SQLiteは個別の保存設計が必要です。

Google Sheetsの通常JSON Storeには、API障害時にローカルJSONへフォールバックするものがあります。Sheetsとローカルファイルは自動で完全同期されるバックアップではありません。実運用前にリストア手順を用意してください。

## ログとトラブルシューティング

- Render Logsで`[start]`、`[bot]`、`[tor-check]`、`Cog読み込みエラー`、`スプレッドシート`、`BCSFE NETWORK`等を確認します。
- `/healthz`が`{"status":"ok"}`を返すことはWebプロセスのヘルス確認です。Discord GatewayやSheetsの正常性をすべて保証するものではありません。
- スラッシュコマンドが表示されない場合は、Bot招待時の`applications.commands`スコープ、`DISCORD_GUILD_ID`、Render起動ログのCommand Syncを確認します。グローバル同期は反映に時間がかかります。
- Discord OAuthが失敗する場合は、Developer Portalに登録したRedirect URLと`REDIRECT_URI`/`DISCORD_REDIRECT_URI`の完全一致を確認します。
- Sheets保存に失敗する場合は、JSON形式の`GOOGLE_CREDENTIALS_JSON`、API有効化、スプレッドシートのサービスアカウント共有、`SPREADSHEET_NAME`を確認します。
- Tor確認が失敗する場合は、Tor起動ログ、`tor_enabled`、`tor_socks_url`、`TSUM_TOR_START`、SOCKS依存の`PySocks`を確認します。Tor SOCKSの9050番へHTTPプロキシとして接続しないでください。
- メールが届かない場合は、Worker URL、Worker/Renderの`MAIL_WEBHOOK_TOKEN`一致、Email Routingの宛先、メールドメイン、Botのチャンネル権限、Workerログを確認します。
- `RUNTIME_DATA_DIR`の書込エラーは、ディレクトリの存在・権限・永続ディスクのマウント先を確認します。

## セキュリティ・運用上の注意

- `.env`、`tsum_bundle/tsum_settings.json`、OAuth/LINE/PayPay/Kyashセッション、サービスアカウント鍵、プロキシ資格情報をGitへコミットしないでください。`.gitignore`にも設定があります。
- OAuth Access/Refresh Token、アカウント認証情報、受信メールは機微データです。Google Sheet、Discordチャンネル、Render Secret、バックアップのアクセス権を必要最小限にします。
- `/バックアップ`の生成物にもOAuthデータが含まれるため、公開フォルダやGitHubへ置かないでください。
- HTTPSを有効にし、本番の`FLASK_SECRET_KEY`と各Identity Keyは十分長いランダム値にして固定します。鍵を変更するとセッションやハッシュ化IDに影響することがあります。
- `PROXY_CHECK_ON_STARTUP=1`の診断は外部IPサービスへ通信します。通常運用で不要なら有効化しないでください。
- 決済/ゲーム/LINE等の外部サービスは仕様・利用条件が変更されることがあります。認証情報を利用者へ共有せず、各サービスの規約と権限範囲を確認してください。
- READMEには秘密情報や実ユーザーのトークンを書かず、設定値は必ずデプロイ環境のSecretとして管理します。

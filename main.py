import os
import json
import asyncio
import hmac
import shutil
import threading
import time
import re
from datetime import datetime
from flask import Flask, request
from werkzeug.middleware.dispatcher import DispatcherMiddleware
import requests
import discord
from interaction_guard import ensure_deferred
from discord import app_commands
from discord.ext import commands, tasks
import gspread
from google.oauth2.service_account import Credentials
from persistent_store import load_json_store, preload_json_stores, save_json_store
from mail_service import deliver_incoming

# -------------------------------------------------------------
# ⚙️ 設定（Render等の共通環境変数から安全に読み込みます）
# -------------------------------------------------------------
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", 0))
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL")
if RENDER_EXTERNAL_URL:
    REDIRECT_URI = f"{RENDER_EXTERNAL_URL}/callback"
else:
    REDIRECT_URI = os.environ.get("REDIRECT_URI", "http://localhost:8080/callback")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
app = Flask(__name__)
BOT_LOOP = None


class AutocatPrefixMiddleware:
    """Mount AUTOCAT below /autocat and rewrite every root-relative app URL."""

    # Covers HTML href/src/action, JavaScript fetch/location strings, JSON URLs,
    # and future routes without maintaining a list of every current endpoint.
    _root_url_re = re.compile(
        rb"(?P<quote>['\"(=])/(?!(?:/|autocat(?:/|['\"?#)`]|$)))(?P<tail>[^'\"`\s<)]*)"
    )

    def __init__(self, application, prefix="/autocat"):
        self.application = application
        self.prefix = prefix.encode("ascii")

    def __call__(self, environ, start_response):
        captured = {}

        def capture_start(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            captured["exc_info"] = exc_info

        body = b"".join(self.application(environ, capture_start))
        headers = list(captured.get("headers", []))
        content_type = next((v.lower() for k, v in headers if k.lower() == "content-type"), "")

        # HTML/JavaScript URLs are fixed in the source files. Do not rewrite
        # JavaScript response bodies at runtime: regex literals and embedded
        # code can otherwise be corrupted and make buttons stop responding.

        rewritten_headers = []
        for key, value in headers:
            if key.lower() in {"location", "service-worker-allowed"} and value.startswith("/") and not value.startswith("/autocat/"):
                value = "/autocat" + value
            if key.lower() == "content-length":
                value = str(len(body))
            rewritten_headers.append((key, value))

        start_response(captured.get("status", "500 Internal Server Error"), rewritten_headers, captured.get("exc_info"))
        return [body]

# にゃんこ大戦争代行Web版。失敗しても既存Discord Botは起動できるよう分離して読み込む。
AUTOCAT_APP = None
try:
    # 新しいRender環境変数を要求せず、既存Botの設定から自動的に補完する。
    _autocat_base_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if _autocat_base_url:
        os.environ.setdefault("PUBLIC_BASE_URL", _autocat_base_url)
        # 既存Botの /callback と衝突させず、代行Web専用のOAuth戻り先を使う。
        os.environ["DISCORD_REDIRECT_URI"] = f"{_autocat_base_url}/autocat/auth/callback"
    os.environ.setdefault("VIP_GUILD_ID", os.environ.get("DISCORD_GUILD_ID", "0"))
    # FLASK_SECRET_KEY未設定でも再起動後にセッションを維持できるよう、既存秘密値を利用する。
    os.environ.setdefault(
        "FLASK_SECRET_KEY",
        os.environ.get("DISCORD_CLIENT_SECRET") or os.environ.get("DISCORD_BOT_TOKEN", "autocat-session-key"),
    )
    import autocat_main as _autocat_module
    AUTOCAT_APP = _autocat_module.app
    AUTOCAT_APP.wsgi_app = AutocatPrefixMiddleware(AUTOCAT_APP.wsgi_app)
    app.wsgi_app = DispatcherMiddleware(app.wsgi_app, {"/autocat": AUTOCAT_APP.wsgi_app})
    print("✅ にゃんこ代行Web版を /autocat に接続しました")
except Exception as exc:
    print(f"⚠️ にゃんこ代行Web版を読み込めませんでした: {type(exc).__name__}: {exc}")

async def global_interaction_check(interaction: discord.Interaction) -> bool:
    # スラッシュコマンドは、処理内容に関係なく最初に保留応答を返す。
    # ボタンやモーダルは各処理側の応答を使う。
    if interaction.type == discord.InteractionType.application_command:
        # 自販機パネル本体はサーバー全員に公開する必要がある。
        is_public_panel = getattr(interaction.command, "name", None) in {
            "有料自販機設置",
            "メールパネル設置",
            "にゃんこ代行",
        }
        await ensure_deferred(interaction, ephemeral=not is_public_panel)
    return True

bot.tree.interaction_check = global_interaction_check

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CheckFailure):
        message = "このコマンドを実行する権限がありません。"
    else:
        print(f"スラッシュコマンドエラー: {error}")
        message = "コマンドの処理中にエラーが発生しました。時間を置いて再試行してください。"
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)

# Cogsフォルダからの拡張機能（PayPay決済等）自動読み込み設定
async def setup_hook():
    # Google APIをコマンド実行中に待たないよう、有料自販機関連データを起動時に先読みする
    await asyncio.to_thread(preload_json_stores, [
        ("server_config", "server_config.json"),
        ("paid_vending_items", "vending_data.json"),
        ("paid_stock_contents", "stock_contents.json"),
        ("paid_stock_notifications", "stock_notification_data.json"),
        ("paid_coupons", "coupon_data.json"),
        ("paid_role_assignments", "role_assignment_data.json"),
        ("paid_used_paypay_links", "used_paypay_links.json"),
        ("paypay_accounts", "paypay_data.json"),
        ("kyash_accounts", "kyash_data.json"),
        ("mail_accounts", "mail_accounts.json"),
        ("points_and_referrals", "points_and_referrals.json"),
    ])
    # 本体側の自販機機能と有料自販機Cogを読み込む
    extensions = ["Cogs.paypay", "Cogs.vending", "Cogs.kyash_cog", "Cogs.mail", "Cogs.points"]
    for extension in extensions:
        try:
            await bot.load_extension(extension)
            print(f"✅ 拡張機能(Cog)を読み込みました: {extension}")
        except Exception as e:
            print(f"❌ Cog読み込みエラー {extension}: {e}")

bot.setup_hook = setup_hook

BACKUP_DIR = "backups"

# -------------------------------------------------------------
# 📊 Googleスプレッドシート接続設定（完全永続化 & サーバー別設定管理）
# -------------------------------------------------------------
SPREADSHEET_NAME = os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB")

def get_gspread_client():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not creds_json:
        return None
    scope = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
    client = gspread.authorize(creds)
    return client

# サーバーごとの設定（ロールID・ログチャンネルIDなど）をスプレッドシートで管理する関数
def load_server_config():
    default_config = {
        "member_role_id": int(os.environ.get("MEMBER_ROLE_ID", 0)),
        "staff_role_id": int(os.environ.get("STAFF_ROLE_ID", 0)),
        "admin_role_id": int(os.environ.get("ADMIN_ROLE_ID", 0)),
        "log_channel_id": int(os.environ.get("LOG_CHANNEL_ID", 0))
    }
    data = load_json_store("server_config", "server_config.json")
    return {**default_config, **data}

def save_server_config(config):
    save_json_store("server_config", config, "server_config.json")



def load_data():
    client = get_gspread_client()
    if not client:
        return {}
    try:
        sheet = client.open(SPREADSHEET_NAME).worksheet("oauth_users")
        data_str = sheet.cell(1, 1).value
        if not data_str:
            return {}
        return json.loads(data_str)
    except Exception as e:
        return {}

def save_data(data):
    client = get_gspread_client()
    if not client:
        print("スプレッドシートクライアントが初期化されていません。")
        return
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        try:
            sheet = spreadsheet.worksheet("oauth_users")
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title="oauth_users", rows=100, cols=20)
        
        sheet.clear()
        data_str = json.dumps(data, ensure_ascii=False)
        sheet.update_cell(1, 1, data_str)
    except Exception as e:
        print(f"スプレッドシート(oauth_users)保存エラー: {e}")

def perform_manual_backup():
    """スプレッドシートからデータを取得し、手動バックアップ（JSON保存）と登録人数を返す関数"""
    try:
        if not os.path.exists(BACKUP_DIR):
            os.makedirs(BACKUP_DIR)
        
        data = load_data()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(BACKUP_DIR, f"oauth_users_backup_{timestamp}.json")
        
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return True, len(data)
    except Exception as e:
        print(f"バックアップエラー: {e}")
        return False, 0

def load_announce_config():
    ANNOUNCE_CONFIG_FILE = "announce_config.json"
    if not os.path.exists(ANNOUNCE_CONFIG_FILE):
        default_config = {
            "channel_id": 0,
            "interval_hours": 24,
            "message": "ショップは24時間稼働中です。\nご用件やチケット作成はチャンネル内のパネルからどうぞ！"
        }
        with open(ANNOUNCE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)
        return default_config
    try:
        with open(ANNOUNCE_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"channel_id": 0, "interval_hours": 24, "message": "ショップ稼働中！"}

def save_announce_config(config):
    ANNOUNCE_CONFIG_FILE = "announce_config.json"
    with open(ANNOUNCE_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=4)

def has_admin_role(member: discord.Member) -> bool:
    if member.guild_permissions.administrator:
        return True
    cfg = load_server_config()
    admin_role_id = cfg.get("admin_role_id", 0)
    if admin_role_id != 0 and any(role.id == admin_role_id for role in member.roles):
        return True
    return False

# -------------------------------------------------------------
# 📢 定期送信機能
# -------------------------------------------------------------
@tasks.loop(hours=24)
async def scheduled_announcement():
    config = load_announce_config()
    channel_id = config.get("channel_id", 0)
    if channel_id == 0:
        return
    channel = bot.get_channel(channel_id)
    if channel:
        embed = discord.Embed(
            title="📢 【自動お知らせ】ショップ稼働中！",
            description=config.get("message", "ショップ稼働中！"),
            color=0xF1C40F
        )
        embed.set_footer(text=f"送信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        await channel.send(embed=embed)

# -------------------------------------------------------------
# 1. 認証用Webサーバー（おしゃれなホームページ＆認証完了画面）
# -------------------------------------------------------------
@app.route("/")
def home():
    return """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>Discord Bot Server</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #2c2f33; color: #ffffff; text-align: center; padding-top: 100px; }
            .card { background: #23272a; max-width: 500px; margin: 0 auto; padding: 40px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
            h1 { color: #7289da; margin-bottom: 10px; }
            p { color: #99aab5; }
            .status { display: inline-block; background: #43b581; width: 12px; height: 12px; border-radius: 50%; margin-right: 6px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>Discord Bot Online</h1>
            <p><span class="status"></span>サーバーおよび自販機システムは正常に稼働中です。</p>
        </div>
    </body>
    </html>
    """

@app.route("/mail/incoming", methods=["POST"])
def mail_incoming():
    expected = os.environ.get("MAIL_WEBHOOK_TOKEN", "")
    received = request.headers.get("X-Mail-Webhook-Token", "")
    if not expected or not hmac.compare_digest(received, expected):
        return {"ok": False, "error": "unauthorized"}, 401

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not payload.get("to"):
        return {"ok": False, "error": "invalid_payload"}, 400
    if BOT_LOOP is None or not BOT_LOOP.is_running():
        return {"ok": False, "error": "bot_not_ready"}, 503

    future = asyncio.run_coroutine_threadsafe(deliver_incoming(bot, payload), BOT_LOOP)
    try:
        future.result(timeout=15)
    except Exception as exc:
        print(f"メールDiscord転送エラー: {exc}")
        return {"ok": False, "error": "delivery_failed"}, 502
    return {"ok": True}, 202


@app.route("/api/access/identify", methods=["POST"])
def legacy_autocat_access_identify():
    """旧版アクセス確認ページからのPOSTを/autocatへ引き継ぐ。"""
    return redirect("/autocat/api/access/identify", code=307)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return '認証がキャンセルされました。'

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post("https://discord.com/api/oauth2/token", data=data, headers=headers)
    json_res = response.json()

    if "access_token" not in json_res:
        return 'トークンの取得に失敗しました。'

    access_token = json_res["access_token"]
    refresh_token = json_res.get("refresh_token")

    user_res = requests.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
    user_data = user_res.json()
    user_id = str(user_data["id"])
    username = user_data["username"]

    # 認証時にスプレッドシートへ自動保存（永続化）
    db = load_data()
    db[user_id] = {
        "name": username,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "verified_at": str(datetime.now())
    }
    save_data(db)

    guild = bot.get_guild(GUILD_ID)
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            cfg = load_server_config()
            member_role_id = cfg.get("member_role_id", 0)
            role = guild.get_role(member_role_id) if member_role_id != 0 else None
            if role and role not in member.roles:
                bot.loop.call_soon_threadsafe(lambda: bot.loop.create_task(member.add_roles(role)))

    return """
    <!DOCTYPE html>
    <html lang="ja">
    <head>
        <meta charset="UTF-8">
        <title>認証完了</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #2c2f33; color: #ffffff; text-align: center; padding-top: 100px; }
            .card { background: #23272a; max-width: 500px; margin: 0 auto; padding: 40px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.3); }
            h1 { color: #43b581; margin-bottom: 10px; }
            p { color: #99aab5; }
        </style>
    </head>
    <body>
        <div class="card">
            <h1>🎉 認証が完了しました！</h1>
            <p>ロールが正常に付与されました。このウィンドウを閉じてDiscordにお戻りください。</p>
        </div>
    </body>
    </html>
    """

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# 2. チケット機能
# -------------------------------------------------------------
class TicketCloseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 チケットを閉じる", style=discord.ButtonStyle.red, custom_id="close_ticket_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 このチャンネルをまもなく削除します...", ephemeral=True)
        await interaction.channel.delete()

class TicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🎫 チケットを作成する", style=discord.ButtonStyle.green, custom_id="create_ticket_btn")
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        category = discord.utils.get(guild.categories, name="【チケット窓口】")
        if not category:
            category = await guild.create_category("【チケット窓口】")

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)
        }

        cfg = load_server_config()
        staff_role_id = cfg.get("staff_role_id", 0)
        admin_role_id = cfg.get("admin_role_id", 0)

        staff_role = guild.get_role(staff_role_id) if staff_role_id != 0 else None
        admin_role = guild.get_role(admin_role_id) if admin_role_id != 0 else None
        
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

        channel_name = f"ticket-{interaction.user.name.lower()}"
        existing_channel = discord.utils.get(category.text_channels, name=channel_name)
        
        if existing_channel:
            await interaction.response.send_message(f"❌ すでにオープンしているチケットがあります: {existing_channel.mention}", ephemeral=True)
            return

        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        close_view = TicketCloseView()
        
        mention_texts = [interaction.user.mention]
        if guild.owner:
            mention_texts.append(guild.owner.mention)
        if staff_role:
            mention_texts.append(staff_role.mention)
        if admin_role:
            mention_texts.append(admin_role.mention)
        
        mention_string = " ".join(mention_texts)

        embed = discord.Embed(
            title="🎫 お問い合わせチケット",
            description=f"スタッフが対応いたしますので、用件を詳しく記入してお待ちください。",
            color=0x2b2d31
        )
        embed.add_field(name="📌 ご利用の流れ", value="1. スタッフからの案内に従う\n2. 詳細を伝える", inline=False)
        
        await channel.send(content=mention_string, embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ チケットを作成しました！ 👉 {channel.mention}", ephemeral=True)

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        base_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else "http://localhost:8080"
        oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={requests.utils.quote(base_url + '/callback')}&response_type=code&scope=identify%20guilds.join"
        self.add_item(discord.ui.Button(label="✅ 認証してロールを受け取る", style=discord.ButtonStyle.link, url=oauth_url))

# -------------------------------------------------------------
# ボット起動イベント
# -------------------------------------------------------------
@bot.event
async def on_ready():
    global BOT_LOOP
    BOT_LOOP = asyncio.get_running_loop()
    print(f"ログインしました: {bot.user.name} (ID: {bot.user.id})")
    
    try:
        if GUILD_ID:
            guild_obj = discord.Object(id=GUILD_ID)
            bot.tree.copy_global_to(guild=guild_obj)
            await bot.tree.sync(guild=guild_obj)
            print("スラッシュコマンドをギルドに同期しました！")
        else:
            await bot.tree.sync()
            print("スラッシュコマンドをグローバルに同期しました！")
    except Exception as e:
        print(f"コマンド同期エラー: {e}")

    config = load_announce_config()
    channel_id = config.get("channel_id", 0)
    hours = config.get("interval_hours", 24)
    if channel_id != 0:
        if not scheduled_announcement.is_running():
            scheduled_announcement.change_interval(hours=hours)
            scheduled_announcement.start()

    bot.add_view(TicketView())
    bot.add_view(TicketCloseView())
    bot.add_view(VerifyView())
    
    

# -------------------------------------------------------------
# スラッシュコマンド一覧（完全網羅）
# -------------------------------------------------------------
@bot.tree.command(name="設定", description="サーバーごとのロールIDやログチャンネルIDを設定・確認します。")
@app_commands.describe(
    member_role="メンバーロール (メンションまたはID)",
    staff_role="スタッフロール (メンションまたはID)",
    admin_role="管理者ロール (メンションまたはID)",
    log_channel="実績・ログチャンネル (メンションまたはID)"
)
@app_commands.checks.has_permissions(administrator=True)
async def config_command(
    interaction: discord.Interaction, 
    member_role: discord.Role = None, 
    staff_role: discord.Role = None, 
    admin_role: discord.Role = None, 
    log_channel: discord.TextChannel = None
):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user) and not interaction.user.guild_permissions.administrator:
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return

    cfg = load_server_config()
    
    if member_role:
        cfg["member_role_id"] = member_role.id
    if staff_role:
        cfg["staff_role_id"] = staff_role.id
    if admin_role:
        cfg["admin_role_id"] = admin_role.id
    if log_channel:
        cfg["log_channel_id"] = log_channel.id

    save_server_config(cfg)

    embed = discord.Embed(
        title="⚙️ サーバー設定が更新されました",
        description="スプレッドシートに正常に保存されました。",
        color=0x2ecc71
    )
    embed.add_field(name="👤 メンバーロール", value=f"<@&{cfg['member_role_id']}>" if cfg['member_role_id'] else "未設定", inline=False)
    embed.add_field(name="🛡️ スタッフロール", value=f"<@&{cfg['staff_role_id']}>" if cfg['staff_role_id'] else "未設定", inline=False)
    embed.add_field(name="👑 管理者ロール", value=f"<@&{cfg['admin_role_id']}>" if cfg['admin_role_id'] else "未設定", inline=False)
    embed.add_field(name="📜 実績/ログチャンネル", value=f"<#{cfg['log_channel_id']}>" if cfg['log_channel_id'] else "未設定", inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="チケット設置", description="チケット作成パネルを送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎫 お問い合わせチケット作成",
        description="下のボタンを押すと、専用のお問い合わせチャンネルが作成されます。",
        color=0x2b2d31
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.followup.send("✅ チケットパネルを送信しました！", ephemeral=True)

@bot.tree.command(name="認証設置", description="認証パネル（ロール付与）を送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    embed = discord.Embed(
        title="✅ 認証パネル",
        description="下のボタンを押してDiscordアカウントを連携すると、メンバーロールが自動で付与されます。",
        color=0x3498DB
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.followup.send("✅ 認証パネルを送信しました！", ephemeral=True)

@bot.tree.command(name="お知らせ設定", description="定期お知らせを設定します。")
@app_commands.describe(hours="何時間おきに送信するか (例: 24)", message="送信するメッセージ内容")
@app_commands.checks.has_permissions(administrator=True)
async def setup_announcement(interaction: discord.Interaction, hours: int, message: str):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    config = {
        "channel_id": interaction.channel.id,
        "interval_hours": hours,
        "message": message
    }
    save_announce_config(config)
    scheduled_announcement.change_interval(hours=hours)
    if not scheduled_announcement.is_running():
        scheduled_announcement.start()
    await interaction.followup.send(f"✅ このチャンネルに {hours}時間おきの定期お知らせを設定しました！\n内容: {message}", ephemeral=True)

@bot.tree.command(name="お知らせ確認", description="現在の定期お知らせの設定状況を確認します。")
@app_commands.checks.has_permissions(administrator=True)
async def check_announcement(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    config = load_announce_config()
    channel_id = config.get("channel_id", 0)
    hours = config.get("interval_hours", 24)
    message = config.get("message", "設定なし")
    
    channel_mention = f"<#{channel_id}>" if channel_id != 0 else "未設定"
    
    embed = discord.Embed(
        title="📢 定期お知らせ 設定状況",
        description="現在保存されている定期お知らせの設定内容です。",
        color=0x3498DB
    )
    embed.add_field(name="📍 送信チャンネル", value=channel_mention, inline=False)
    embed.add_field(name="⏳ 送信間隔", value=f"{hours} 時間おき", inline=False)
    embed.add_field(name="💬 送信メッセージ", value=message, inline=False)
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="お知らせテスト", description="定期お知らせのテスト送信を行います（現在の設定内容を今すぐ送信）。")
@app_commands.checks.has_permissions(administrator=True)
async def test_announcement(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    config = load_announce_config()
    channel_id = config.get("channel_id", 0)
    if channel_id == 0:
        await interaction.followup.send("❌ 定期お知らせの送信チャンネルが設定されていません。", ephemeral=True)
        return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("❌ 設定されたチャンネルが見つかりませんでした。", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📢 【テスト送信】ショップ稼働中！",
        description=config.get("message", "ショップ稼働中！"),
        color=0xF1C40F
    )
    embed.set_footer(text=f"テスト送信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await channel.send(embed=embed)
    await interaction.followup.send(f"✅ 設定されているチャンネル ({channel.mention}) にテスト送信を行いました！", ephemeral=True)

@bot.tree.command(name="お知らせ解除", description="定期お知らせの設定を消去し、自動送信を停止します。")
@app_commands.checks.has_permissions(administrator=True)
async def clear_announcement(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    if scheduled_announcement.is_running():
        scheduled_announcement.stop()
    
    default_config = {
        "channel_id": 0,
        "interval_hours": 24,
        "message": "ショップは24時間稼働中です。\nご用件やチケット作成はチャンネル内のパネルからどうぞ！"
    }
    save_announce_config(default_config)
    await interaction.followup.send("✅ 定期お知らせの設定を消去し、自動送信を停止しました。", ephemeral=True)

@bot.tree.command(name="バックアップ", description="メンバーバックアップを手動で強制実行し、登録人数を表示します。")
@app_commands.checks.has_permissions(administrator=True)
async def backup_command(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    await ensure_deferred(interaction, ephemeral=True)
    success, count = perform_manual_backup()
    
    if success:
        await interaction.followup.send(f"✅ メンバーバックアップを強制実行しました！\n📊 現在の登録ユーザー数: **{count} 人**", ephemeral=True)
    else:
        await interaction.followup.send("❌ バックアップの実行に失敗しました。", ephemeral=True)

@bot.tree.command(name="一括呼び戻し", description="スプレッドシートに登録されている全ユーザーのアクセストークンを使い、サーバーに一斉呼び戻します。")
@app_commands.checks.has_permissions(administrator=True)
async def force_join(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return

    await ensure_deferred(interaction, ephemeral=True)
    db = load_data()
    if not db:
        await interaction.followup.send("❌ スプレッドシートに認証データが登録されていません。", ephemeral=True)
        return

    guild = interaction.guild
    success_count = 0
    fail_count = 0

    cfg = load_server_config()
    member_role_id = cfg.get("member_role_id", 0)

    for user_id, user_info in db.items():
        access_token = user_info.get("access_token")
        if not access_token:
            fail_count += 1
            continue

        url = f"https://discord.com/api/v10/guilds/{guild.id}/members/{user_id}"
        headers = {
            "Authorization": f"Bot {BOT_TOKEN}",
            "Content-Type": "application/json"
        }
        payload = {
            "access_token": access_token
        }
        if member_role_id:
            payload["roles"] = [member_role_id]

        response = requests.put(url, headers=headers, json=payload)
        if response.status_code in [201, 204]:
            success_count += 1
        else:
            fail_count += 1

    await interaction.followup.send(
        f"🔄 **一斉強制呼び出しが完了しました！**\n"
        f"✅ 成功: **{success_count} 人**\n"
        f"❌ 失敗（トークン期限切れ等）: **{fail_count} 人**",
        ephemeral=True
    )

@bot.tree.command(name="発言", description="ボットに指定した言葉を喋らせます。")
@app_commands.describe(message="ボットに発言させたい言葉")
@app_commands.checks.has_permissions(administrator=True)
async def say_command(interaction: discord.Interaction, message: str):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    await interaction.channel.send(message)
    await interaction.followup.send("✅ メッセージを送信しました。", ephemeral=True)

@bot.tree.command(name="ヘルプ", description="ボットのコマンド一覧と使い方を表示します。")
async def help_cmd(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    embed = discord.Embed(
        title="🤖 ボット機能・コマンド一覧",
        description="このサーバーで利用できるコマンドと機能のご案内です。",
        color=0x3498DB
    )
    embed.add_field(name="⚙️ サーバー設定", value="`/設定` - ロールやログチャンネルを設定・確認します。", inline=False)
    embed.add_field(name="🎫 チケット機能", value="`/チケット設置` - お問い合わせ用チケット作成パネルを送信します。", inline=False)
    embed.add_field(name="✅ 認証機能", value="`/認証設置` - 認証＆ロール付与パネルを送信します。\n※認証時にスプレッドシートへデータが永続化されます。", inline=False)
    embed.add_field(name="💰 有料自販機機能（PayPay・Kyash）", value="`/有料自販機作成` - 有料自販機を作成\n`/有料商品追加` - 価格付きの商品を追加\n`/有料自販機設置` - 有料自販機を設置\n`/有料在庫追加` - 在庫を追加\n`/有料商品情報変更` - PayPay価格・Kyash価格を変更\n`/有料自販機パネル更新` - パネルを更新\n`/有料在庫引出` - 在庫を引き出す\n`/有料在庫内容確認` - 在庫を確認\n`/有料商品削除` - 商品を削除\n`/有料自販機削除` - 有料自販機を削除\n`/有料公開ログ設定`・`/有料購入ログ設定`・`/有料非公開ログ設定` - 購入ログ設定\n`/有料自販機クーポン作成`・`/有料自販機クーポン削除`・`/有料自販機クーポン一覧` - クーポン管理", inline=False)
    embed.add_field(name="💳 決済アカウント", value="`/ペイペイログイン` - PayPayを登録\n`/ペイペイログアウト` - PayPay情報を削除\n`/ペイペイプロキシ設定` - 通信設定\n`/キャッシュログイン` - Kyashログインを開始\n`/キャッシュ認証` - Kyashの認証コードを入力", inline=False)
    embed.add_field(name="📢 定期お知らせ", value="`/お知らせ設定` - 定期お知らせを設定します。\n`/お知らせ確認` - 設定状況を確認します。\n`/お知らせテスト` - テスト送信をします。\n`/お知らせ解除` - 設定を消去して停止します。", inline=False)
    embed.add_field(name="👑 実績管理", value="実績チャンネル（ログチャンネル）の投稿数を自動カウントし、チャンネル名を `👑｜実績ー〇〇` に自動更新します。", inline=False)
    embed.add_field(name="💬 発言機能", value="`/発言` - ボットに指定した言葉を喋らせます。", inline=False)
    embed.add_field(name="✉️ メール機能", value="`/メールパネル設置` - メールアドレス発行パネルを設置します。Gmailの4桁エイリアスにも対応しています。", inline=False)
    embed.add_field(name="🎁 ポイント・友達招待", value="`/ポイントパネル設置`（管理者）- 客がボタンで使える説明パネルを設置\n`/ポイント`・`/ポイント履歴` - 残高や履歴を確認\n`/招待コード`・`/招待登録` - 招待コードを発行・登録\n`/ポイントランキング` - ランキングを表示\n`/ユーザー情報`（管理者）- 購入・支払額・ポイント・招待情報を確認\n※購入確認画面の「ポイントを使う」から1ポイント＝1円で値引き可能", inline=False)
    embed.add_field(name="🐱 にゃんこ代行", value="`/にゃんこ代行` - 完全版の代行画面を開きます。引き継ぎコード・認証番号・各種設定をWeb画面で指定できます。", inline=False)
    embed.add_field(name="💾 バックアップ＆呼び出し", value="`/バックアップ` - メンバーデータを手動でバックアップし、登録人数を表示します。\n`/一括呼び戻し` - 登録されている全ユーザーをサーバーに一斉呼び戻しします。", inline=False)
    embed.add_field(name="💥 チャンネル管理", value="`/チャンネル再作成` - 現在のチャンネルを初期化（作り直し）します。", inline=False)
    
    await interaction.followup.send(embed=embed, ephemeral=True)


class AutocatLinkView(discord.ui.View):
    def __init__(self, base_url: str):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="にゃんこ代行画面を開く", style=discord.ButtonStyle.link, url=f"{base_url}/autocat/"))
        self.add_item(discord.ui.Button(label="Discordログイン", style=discord.ButtonStyle.link, url=f"{base_url}/autocat/auth/login?next=/autocat/"))
        self.add_item(discord.ui.Button(label="VIP管理画面ログイン", style=discord.ButtonStyle.link, url=f"{base_url}/autocat/auth/login?next=/autocat/admin/vip"))


@bot.tree.command(name="にゃんこ代行", description="完全版のにゃんこ大戦争代行画面を開きます")
async def autocat_link(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if AUTOCAT_APP is None:
        await interaction.followup.send("代行機能の準備に失敗しています。Renderのログと依存関係を確認してください。", ephemeral=True)
        return
    base_url = (os.environ.get("RENDER_EXTERNAL_URL") or os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if not base_url:
        await interaction.followup.send("RENDER_EXTERNAL_URLまたはPUBLIC_BASE_URLが未設定です。", ephemeral=True)
        return
    panel_message = await interaction.followup.send(
        "完全版のにゃんこ代行画面です。ログイン後、引き継ぎコード・認証番号・各種設定を入力できます。",
        view=AutocatLinkView(base_url),
        ephemeral=False,
        wait=True,
    )
    try:
        await panel_message.pin(reason="にゃんこ代行の固定パネル")
    except (discord.Forbidden, discord.HTTPException):
        # ピン留め権限がない場合でも、公開パネル自体は残す。
        pass

@bot.tree.command(name="チャンネル再作成", description="現在のチャンネルを削除し、同じ設定の新しいチャンネルに作り直します。")
@app_commands.checks.has_permissions(administrator=True)
async def nuke(interaction: discord.Interaction):
    await ensure_deferred(interaction, ephemeral=True)
    if not has_admin_role(interaction.user):
        await interaction.followup.send("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    await interaction.followup.send("💥 チャンネルを初期化しています...", ephemeral=True)
    
    channel = interaction.channel
    position = channel.position
    category = channel.category
    overwrites = channel.overwrites
    topic = channel.topic
    slowmode_delay = channel.slowmode_delay
    nsfw = channel.is_nsfw()
    
    new_channel = await channel.clone(name=channel.name, reason="Nuke command executed")
    await new_channel.edit(position=position, topic=topic, slowmode_delay=slowmode_delay, nsfw=nsfw)
    await channel.delete()
    
    await new_channel.send(f"💥 チャンネルが初期化されました（実行者: {interaction.user.mention}）")

# -------------------------------------------------------------
# 起動処理
# -------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    bot.run(os.environ['DISCORD_BOT_TOKEN'])

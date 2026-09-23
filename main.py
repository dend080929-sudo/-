import os
import json
import shutil
import threading
import time
from datetime import datetime
from flask import Flask, request
import requests
import discord
from discord import app_commands
from discord.ext import commands, tasks
import gspread
from google.oauth2.service_account import Credentials

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

# Cogsフォルダからの拡張機能（PayPay決済等）自動読み込み設定
async def setup_hook():
    # 本体側の自販機機能と旧Cogs.vendingのコマンド重複を避け、PayPay Cogのみ読み込む
    extensions = ["Cogs.paypay", "Cogs.vending", "Cogs.kyash_cog"]
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
    client = get_gspread_client()
    default_config = {
        "member_role_id": int(os.environ.get("MEMBER_ROLE_ID", 0)),
        "staff_role_id": int(os.environ.get("STAFF_ROLE_ID", 0)),
        "admin_role_id": int(os.environ.get("ADMIN_ROLE_ID", 0)),
        "log_channel_id": int(os.environ.get("LOG_CHANNEL_ID", 0))
    }
    if not client:
        return default_config
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        try:
            sheet = spreadsheet.worksheet("server_config")
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title="server_config", rows=10, cols=10)
            data_str = json.dumps(default_config, ensure_ascii=False)
            sheet.update_cell(1, 1, data_str)
            return default_config
        
        data_str = sheet.cell(1, 1).value
        if not data_str:
            return default_config
        return json.loads(data_str)
    except Exception as e:
        print(f"スプレッドシート(server_config)読み込みエラー: {e}")
        return default_config

def save_server_config(config):
    client = get_gspread_client()
    if not client:
        print("スプレッドシートクライアントが初期化されていません。")
        return
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        try:
            sheet = spreadsheet.worksheet("server_config")
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title="server_config", rows=10, cols=10)
        
        sheet.clear()
        data_str = json.dumps(config, ensure_ascii=False)
        sheet.update_cell(1, 1, data_str)
    except Exception as e:
        print(f"スプレッドシート(server_config)保存エラー: {e}")

def load_vending():
    client = get_gspread_client()
    if not client:
        return {}
    try:
        sheet = client.open(SPREADSHEET_NAME).worksheet("vending_items")
        data_str = sheet.cell(1, 1).value
        if not data_str:
            return {}
        return json.loads(data_str)
    except Exception as e:
        print(f"スプレッドシート(vending)読み込みエラー: {e}")
        return {}

def save_vending(data):
    client = get_gspread_client()
    if not client:
        print("スプレッドシートクライアントが初期化されていません。")
        return
    try:
        spreadsheet = client.open(SPREADSHEET_NAME)
        try:
            sheet = spreadsheet.worksheet("vending_items")
        except gspread.exceptions.WorksheetNotFound:
            sheet = spreadsheet.add_worksheet(title="vending_items", rows=100, cols=20)
        
        sheet.clear()
        data_str = json.dumps(data, ensure_ascii=False)
        sheet.update_cell(1, 1, data_str)
    except Exception as e:
        print(f"スプレッドシート(vending)保存エラー: {e}")

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
# 👑 実績チャンネルのカウント自動更新関数
# -------------------------------------------------------------
async def update_achievement_channel_name(guild: discord.Guild):
    cfg = load_server_config()
    log_channel_id = cfg.get("log_channel_id", 0)
    if log_channel_id == 0:
        return
    log_channel = guild.get_channel(log_channel_id)
    if not log_channel:
        return
    
    try:
        count = 0
        async for _ in log_channel.history(limit=None):
            count += 1
        
        new_name = f"👑｜実績ー{count}"
        if log_channel.name != new_name:
            await log_channel.edit(name=new_name)
    except Exception as e:
        print(f"チャンネル名更新エラー: {e}")

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

# -------------------------------------------------------------
# 3. 自販機システム（金額なし・在庫ID表示・エラー対策版）
# -------------------------------------------------------------
class ItemAddModal(discord.ui.Modal):
    def __init__(self, machine_name: str):
        super().__init__(title=f"自販機追加: {machine_name}")
        self.machine_name = machine_name

    item_name = discord.ui.TextInput(label="商品名", placeholder="例: 自動代行ソースコード", max_length=100)
    item_stock = discord.ui.TextInput(label="在庫数（半角数字 または ∞）", placeholder="例: 5 または ∞", max_length=10, default="∞")
    item_desc = discord.ui.TextInput(label="商品説明（誰でも見えます）", style=discord.TextStyle.paragraph, placeholder="例: 自動代行ソースコードです。", max_length=500)
    item_content = discord.ui.TextInput(label="商品の中身（購入者にだけ送られます）", style=discord.TextStyle.paragraph, placeholder="例: GoogleドライブのURLやシリアルコード", max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if not has_admin_role(interaction.user):
            await interaction.followup.send("❌ 権限がありません。", ephemeral=True)
            return

        stock_val = self.item_stock.value.strip()
        if stock_val in ["∞", "inf", "Infinity", "infinity"]:
            stock = 99
        else:
            try:
                stock = int(stock_val)
            except ValueError:
                await interaction.followup.send("❌ 在庫数は数字または「∞」で入力してください。", ephemeral=True)
                return

        data = load_vending()
        if self.machine_name not in data:
            data[self.machine_name] = {}
        
        machine_items = data[self.machine_name]
        new_id = str(max([int(k) for k in machine_items.keys()] + [0]) + 1)
        
        machine_items[new_id] = {
            "name": self.item_name.value,
            "desc": self.item_desc.value,
            "content": self.item_content.value,
            "stock": stock,
            "sold": 0
        }
        save_vending(data)
        
        await interaction.followup.send(f"✅ 自販機「**{self.machine_name}**」に商品を追加しました！ (ID: `{new_id}`)", ephemeral=True)

class PurchaseConfirmView(discord.ui.View):
    def __init__(self, machine_name: str, item_id: str, item_data: dict):
        super().__init__(timeout=180)
        self.machine_name = machine_name
        self.item_id = item_id
        self.item_data = item_data

    @discord.ui.button(label="購入確定", style=discord.ButtonStyle.green, custom_id="confirm_purchase_btn")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        data = load_vending()
        if self.machine_name not in data or self.item_id not in data[self.machine_name]:
            await interaction.followup.send("❌ この商品は存在しません。", ephemeral=True)
            return

        item = data[self.machine_name][self.item_id]
        current_stock = item.get("stock", 99)
        if current_stock != 99 and current_stock <= 0:
            await interaction.followup.send("❌ 申し訳ありません。この商品は売り切れ（在庫切れ）です。", ephemeral=True)
            return

        item["sold"] += 1
        if current_stock != 99:
            item["stock"] -= 1
        save_vending(data)

        secret_content = item.get('content', '（中身が設定されていません）')
        
        embed = discord.Embed(
            title="✅ 購入（受取）が完了しました！",
            description=f"自販機: **{self.machine_name}**\n商品: **{item['name']}**",
            color=0x00ff00
        )
        embed.add_field(name="📦 あなたの商品の中身", value=secret_content, inline=False)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

        cfg = load_server_config()
        log_channel_id = cfg.get("log_channel_id", 0)
        if log_channel_id != 0:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="🎉 取引実績・受取通知",
                    description=f"**自販機**: {self.machine_name}\n**購入者**: {interaction.user.mention}\n**商品名**: {item['name']}",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                await log_channel.send(embed=log_embed)
                await update_achievement_channel_name(interaction.guild)

class VendingSelect(discord.ui.Select):
    def __init__(self, machine_name: str):
        self.machine_name = machine_name
        data = load_vending()
        items = data.get(machine_name, {})
        options = []
        if not items:
            options.append(discord.SelectOption(label="商品がありません", value="none"))
        else:
            for item_id, info in items.items():
                stock_val = info.get('stock', 99)
                stock_str = "∞" if stock_val == 99 else str(stock_val)
                options.append(discord.SelectOption(
                    label=info["name"][:25],
                    value=item_id,
                    description=f"在庫: {stock_str}個"
                ))
        super().__init__(placeholder="商品を選択してください", min_values=1, max_values=1, options=options, custom_id=f"vending_select_{machine_name}")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        if self.values[0] == "none":
            await interaction.followup.send("❌ 商品がありません。", ephemeral=True)
            return

        data = load_vending()
        items = data.get(self.machine_name, {})
        item_id = self.values[0]
        if item_id in items:
            item = items[item_id]
            stock_val = item.get('stock', 99)
            if stock_val != 99 and stock_val <= 0:
                await interaction.followup.send("❌ この商品は現在売り切れです。", ephemeral=True)
                return

            embed = discord.Embed(title=f"購入確認 ({self.machine_name})", color=0x2b2d31)
            embed.add_field(name="商品名", value=item["name"], inline=False)
            embed.add_field(name="商品説明", value=item["desc"], inline=False)
            view = PurchaseConfirmView(self.machine_name, item_id, item)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)

class VendingMainView(discord.ui.View):
    def __init__(self, machine_name: str):
        super().__init__(timeout=None)
        self.machine_name = machine_name

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.green, custom_id="vending_buy_main_btn")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        view = discord.ui.View()
        view.add_item(VendingSelect(self.machine_name))
        await interaction.followup.send("セレクトメニューから商品を選択してください。", view=view, ephemeral=True)

    @discord.ui.button(label="🔍 在庫・販売数の確認", style=discord.ButtonStyle.blurple, custom_id="vending_stock_main_btn")
    async def stock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        
        data = load_vending()
        items = data.get(self.machine_name, {})
        embed = discord.Embed(title=f"📦 在庫・販売数チェッカー ({self.machine_name})", color=0x3498DB)
        if not items:
            embed.description = "現在登録されている商品はありません。"
        else:
            for i_id, info in items.items():
                stock_val = info.get('stock', 99)
                stock_str = "∞" if stock_val == 99 else str(stock_val)
                field_title = f"[ID: {i_id}] {info['name']}"
                field_value = f"在庫: {stock_str}個 | 販売数: {info.get('sold', 0)}個"
                embed.add_field(name=field_title, value=field_value, inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="➕ 商品を追加する", style=discord.ButtonStyle.gray, custom_id="vending_add_modal_btn")
    async def add_item_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_role(interaction.user):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
            return
        await interaction.response.send_modal(ItemAddModal(self.machine_name))

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
    
    vending_data = load_vending()
    for machine_name in vending_data.keys():
        bot.add_view(VendingMainView(machine_name))
    
    for guild in bot.guilds:
        await update_achievement_channel_name(guild)

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
    if not has_admin_role(interaction.user) and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
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

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="チケット設置", description="チケット作成パネルを送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    embed = discord.Embed(
        title="🎫 お問い合わせチケット作成",
        description="下のボタンを押すと、専用のお問い合わせチャンネルが作成されます。",
        color=0x2b2d31
    )
    await interaction.channel.send(embed=embed, view=TicketView())
    await interaction.response.send_message("✅ チケットパネルを送信しました！", ephemeral=True)

@bot.tree.command(name="認証設置", description="認証パネル（ロール付与）を送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    embed = discord.Embed(
        title="✅ 認証パネル",
        description="下のボタンを押してDiscordアカウントを連携すると、メンバーロールが自動で付与されます。",
        color=0x3498DB
    )
    await interaction.channel.send(embed=embed, view=VerifyView())
    await interaction.response.send_message("✅ 認証パネルを送信しました！", ephemeral=True)

@bot.tree.command(name="お知らせ設定", description="定期お知らせを設定します。")
@app_commands.describe(hours="何時間おきに送信するか (例: 24)", message="送信するメッセージ内容")
@app_commands.checks.has_permissions(administrator=True)
async def setup_announcement(interaction: discord.Interaction, hours: int, message: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
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
    await interaction.response.send_message(f"✅ このチャンネルに {hours}時間おきの定期お知らせを設定しました！\n内容: {message}", ephemeral=True)

@bot.tree.command(name="お知らせ確認", description="現在の定期お知らせの設定状況を確認します。")
@app_commands.checks.has_permissions(administrator=True)
async def check_announcement(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
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
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="お知らせテスト", description="定期お知らせのテスト送信を行います（現在の設定内容を今すぐ送信）。")
@app_commands.checks.has_permissions(administrator=True)
async def test_announcement(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    config = load_announce_config()
    channel_id = config.get("channel_id", 0)
    if channel_id == 0:
        await interaction.response.send_message("❌ 定期お知らせの送信チャンネルが設定されていません。", ephemeral=True)
        return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.response.send_message("❌ 設定されたチャンネルが見つかりませんでした。", ephemeral=True)
        return
    
    embed = discord.Embed(
        title="📢 【テスト送信】ショップ稼働中！",
        description=config.get("message", "ショップ稼働中！"),
        color=0xF1C40F
    )
    embed.set_footer(text=f"テスト送信時刻: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    await channel.send(embed=embed)
    await interaction.response.send_message(f"✅ 設定されているチャンネル ({channel.mention}) にテスト送信を行いました！", ephemeral=True)

@bot.tree.command(name="お知らせ解除", description="定期お知らせの設定を消去し、自動送信を停止します。")
@app_commands.checks.has_permissions(administrator=True)
async def clear_announcement(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    if scheduled_announcement.is_running():
        scheduled_announcement.stop()
    
    default_config = {
        "channel_id": 0,
        "interval_hours": 24,
        "message": "ショップは24時間稼働中です。\nご用件やチケット作成はチャンネル内のパネルからどうぞ！"
    }
    save_announce_config(default_config)
    await interaction.response.send_message("✅ 定期お知らせの設定を消去し、自動送信を停止しました。", ephemeral=True)

@bot.tree.command(name="簡易自販機設置", description="自販機パネルを送信します（既存の保存済み自販機から選択、または新規作成可能）。")
@app_commands.describe(machine_name="新しく作成する場合の自販機名（既存から選ぶ場合は空欄でもOK）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_vending(interaction: discord.Interaction, machine_name: str = None):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    data = load_vending()

    if machine_name:
        if machine_name not in data:
            data[machine_name] = {}
            save_vending(data)

        items = data[machine_name]
        vending_embed = discord.Embed(title=f"🎪 自販機: {machine_name}", description="以下のメニューまたはボタンから商品をご利用いただけます。", color=0x2b2d31)
        if not items:
            vending_embed.add_field(name="お知らせ", value="現在登録されている商品はございません。", inline=False)
        else:
            for i_id, info in items.items():
                field_value = f"商品説明: {info.get('desc', 'なし')}"
                vending_embed.add_field(name=info["name"], value=field_value, inline=False)
                
        await interaction.channel.send(embed=vending_embed, view=VendingMainView(machine_name))
        await interaction.response.send_message(f"✅ 自販機パネル（識別名: **{machine_name}**）を設置しました！", ephemeral=True)
        return

    if not data:
        await interaction.response.send_message("❌ 保存されている自販機がありません。コマンドの引数に新しい自販機名を入力して作成してください。(例: `/簡易自販機設置 machine_name:メイン自販機`)", ephemeral=True)
        return

    class VendingMachineSelectView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)
            options = []
            for name in data.keys():
                options.append(discord.SelectOption(label=name[:25], value=name, description=f"登録商品数: {len(data[name])}個"))
            
            select = discord.ui.Select(placeholder="設置する自販機を選択してください", min_values=1, max_values=1, options=options)
            
            async def select_callback(select_interaction: discord.Interaction):
                selected_name = select.values[0]
                if not select_interaction.response.is_done():
                    await select_interaction.response.defer(ephemeral=True)

                items = data.get(selected_name, {})
                vending_embed = discord.Embed(title=f"🎪 自販機: {selected_name}", description="以下のメニューまたはボタンから商品をご利用いただけます。", color=0x2b2d31)
                if not items:
                    vending_embed.add_field(name="お知らせ", value="現在登録されている商品はございません。", inline=False)
                else:
                    for i_id, info in items.items():
                        field_value = f"商品説明: {info.get('desc', 'なし')}"
                        vending_embed.add_field(name=info["name"], value=field_value, inline=False)
                        
                await select_interaction.channel.send(embed=vending_embed, view=VendingMainView(selected_name))
                await select_interaction.followup.send(f"✅ 保存済み自販機「**{selected_name}**」のパネルを設置しました！", ephemeral=True)

            select.callback = select_callback
            self.add_item(select)

    await interaction.response.send_message("👇 設置したい保存済みの自販機を選択してください：", view=VendingMachineSelectView(), ephemeral=True)

@bot.tree.command(name="簡易自販機一覧", description="指定した自販機の登録商品一覧を確認します。")
@app_commands.describe(machine_name="確認したい自販機の名前")
@app_commands.checks.has_permissions(administrator=True)
async def vending_list(interaction: discord.Interaction, machine_name: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    data = load_vending()
    items = data.get(machine_name, {})
    embed = discord.Embed(title=f"🎪 登録商品一覧 ({machine_name})", color=0x3498DB)
    if not items:
        embed.description = "この自販機には商品が登録されていません。"
    for i_id, info in items.items():
        stock_val = info.get('stock', 99)
        stock_str = "∞" if stock_val == 99 else str(stock_val)
        embed.add_field(name=f"ID: {i_id}", value=f"商品名: {info['name']} | 在庫: {stock_str}個", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="簡易商品削除", description="指定した自販機から特定の商品を削除します。")
@app_commands.describe(machine_name="対象の自販機名", item_id="削除する商品のID (例: 1)")
@app_commands.checks.has_permissions(administrator=True)
async def vending_delete(interaction: discord.Interaction, machine_name: str, item_id: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    data = load_vending()
    if machine_name in data and item_id in data[machine_name]:
        deleted_name = data[machine_name][item_id]["name"]
        del data[machine_name][item_id]
        save_vending(data)
        await interaction.response.send_message(f"✅ 自販機「{machine_name}」の商品「{deleted_name}」(ID: {item_id}) を削除しました！", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 指定された自販機名または商品IDが見つかりませんでした。", ephemeral=True)

@bot.tree.command(name="簡易自販機削除", description="指定した自販機自体（登録されている全商品データ含む）を削除します。")
@app_commands.describe(machine_name="削除したい自販機の名前")
@app_commands.checks.has_permissions(administrator=True)
async def vending_machine_delete(interaction: discord.Interaction, machine_name: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    data = load_vending()
    if machine_name in data:
        del data[machine_name]
        save_vending(data)
        await interaction.response.send_message(f"🗑️ 自販機「**{machine_name}**」をデータごと完全に削除しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 指定された自販機名「{machine_name}」が見つかりませんでした。", ephemeral=True)

@bot.tree.command(name="バックアップ", description="メンバーバックアップを手動で強制実行し、登録人数を表示します。")
@app_commands.checks.has_permissions(administrator=True)
async def backup_command(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    success, count = perform_manual_backup()
    
    if success:
        await interaction.followup.send(f"✅ メンバーバックアップを強制実行しました！\n📊 現在の登録ユーザー数: **{count} 人**", ephemeral=True)
    else:
        await interaction.followup.send("❌ バックアップの実行に失敗しました。", ephemeral=True)

@bot.tree.command(name="一括呼び戻し", description="スプレッドシートに登録されている全ユーザーのアクセストークンを使い、サーバーに一斉呼び戻します。")
@app_commands.checks.has_permissions(administrator=True)
async def force_join(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
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
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    await interaction.channel.send(message)
    await interaction.response.send_message("✅ メッセージを送信しました。", ephemeral=True)

@bot.tree.command(name="ヘルプ", description="ボットのコマンド一覧と使い方を表示します。")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 ボット機能・コマンド一覧",
        description="このサーバーで利用できるコマンドと機能のご案内です。",
        color=0x3498DB
    )
    embed.add_field(name="⚙️ サーバー設定", value="`/設定` - ロールやログチャンネルを設定・確認します。", inline=False)
    embed.add_field(name="🎫 チケット機能", value="`/チケット設置` - お問い合わせ用チケット作成パネルを送信します。", inline=False)
    embed.add_field(name="✅ 認証機能", value="`/認証設置` - 認証＆ロール付与パネルを送信します。\n※認証時にスプレッドシートへデータが永続化されます。", inline=False)
    embed.add_field(name="🎪 無料・簡易自販機機能", value="`/簡易自販機設置` - 簡易自販機パネルを設置します。\n`/簡易自販機一覧` - 登録商品一覧の確認\n`/簡易商品削除` - 商品の削除\n`/簡易自販機削除` - 自販機自体の削除", inline=False)
    embed.add_field(name="💰 有料自販機機能（PayPay・Kyash）", value="`/有料自販機作成` - 有料自販機を作成\n`/有料商品追加` - 価格付きの商品を追加\n`/有料自販機設置` - 有料自販機を設置\n`/有料在庫追加` - 在庫を追加\n`/有料商品情報変更` - PayPay価格・Kyash価格を変更\n`/有料自販機パネル更新` - パネルを更新\n`/有料在庫引出` - 在庫を引き出す\n`/有料在庫内容確認` - 在庫を確認\n`/有料商品削除` - 商品を削除\n`/有料自販機削除` - 有料自販機を削除\n`/有料公開ログ設定`・`/有料購入ログ設定`・`/有料非公開ログ設定` - 購入ログ設定\n`/有料自販機クーポン作成`・`/有料自販機クーポン削除`・`/有料自販機クーポン一覧` - クーポン管理", inline=False)
    embed.add_field(name="💳 決済アカウント", value="`/ペイペイログイン` - PayPayを登録\n`/ペイペイログアウト` - PayPay情報を削除\n`/ペイペイプロキシ設定` - 通信設定\n`/キャッシュログイン` - Kyashログインを開始\n`/キャッシュ認証` - Kyashの認証コードを入力", inline=False)
    embed.add_field(name="📢 定期お知らせ", value="`/お知らせ設定` - 定期お知らせを設定します。\n`/お知らせ確認` - 設定状況を確認します。\n`/お知らせテスト` - テスト送信をします。\n`/お知らせ解除` - 設定を消去して停止します。", inline=False)
    embed.add_field(name="👑 実績管理", value="実績チャンネル（ログチャンネル）の投稿数を自動カウントし、チャンネル名を `👑｜実績ー〇〇` に自動更新します。", inline=False)
    embed.add_field(name="💬 発言機能", value="`/発言` - ボットに指定した言葉を喋らせます。", inline=False)
    embed.add_field(name="💾 バックアップ＆呼び出し", value="`/バックアップ` - メンバーデータを手動でバックアップし、登録人数を表示します。\n`/一括呼び戻し` - 登録されている全ユーザーをサーバーに一斉呼び戻しします。", inline=False)
    embed.add_field(name="💥 チャンネル管理", value="`/チャンネル再作成` - 現在のチャンネルを初期化（作り直し）します。", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="チャンネル再作成", description="現在のチャンネルを削除し、同じ設定の新しいチャンネルに作り直します。")
@app_commands.checks.has_permissions(administrator=True)
async def nuke(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    await interaction.response.send_message("💥 チャンネルを初期化しています...", ephemeral=True)
    
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

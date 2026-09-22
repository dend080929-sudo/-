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
# ⚙️ 設定（Renderなどの環境変数から安全に読み込みます）
# -------------------------------------------------------------
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", 0))

MEMBER_ROLE_ID = int(os.environ.get("MEMBER_ROLE_ID", 0))
STAFF_ROLE_ID = int(os.environ.get("STAFF_ROLE_ID", 0))
ADMIN_ROLE_ID = int(os.environ.get("ADMIN_ROLE_ID", 0))

LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

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

DATA_FILE = "oauth_users.json"
BACKUP_DIR = "backups"

# -------------------------------------------------------------
# 📊 Googleスプレッドシート接続設定
# -------------------------------------------------------------
# Renderの環境変数「GOOGLE_CREDENTIALS_JSON」にサービスアカウントのJSONの中身をそのまま貼り付けます
# スプレッドシートのタイトル（ファイル名）を環境変数「SPREADSHEET_NAME」または直接指定します
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
        print(f"スプレッドシート読み込みエラー: {e}")
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
        print(f"スプレッドシート保存エラー: {e}")

def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

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
    if ADMIN_ROLE_ID != 0 and any(role.id == ADMIN_ROLE_ID for role in member.roles):
        return True
    return False

# -------------------------------------------------------------
# 🔄 バックアップ機能
# -------------------------------------------------------------
def perform_backup():
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if os.path.exists(DATA_FILE):
        shutil.copy(DATA_FILE, os.path.join(BACKUP_DIR, f"oauth_users_backup_{timestamp}.json"))

@tasks.loop(hours=12)
async def auto_backup_task():
    perform_backup()

@bot.event
async def on_member_join(member):
    perform_backup()

# -------------------------------------------------------------
# 👑 実績チャンネルのカウント自動更新関数
# -------------------------------------------------------------
async def update_achievement_channel_name(guild: discord.Guild):
    if LOG_CHANNEL_ID == 0:
        return
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
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
# 1. 認証用Webサーバー
# -------------------------------------------------------------
@app.route("/")
def home():
    return "Bot is running!"

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

    db = load_data()
    db[user_id] = {
        "name": username,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "verified_at": str(datetime.now())
    }
    save_data(db)
    perform_backup()

    guild = bot.get_guild(GUILD_ID)
    if guild:
        member = guild.get_member(int(user_id))
        if member:
            role = guild.get_role(MEMBER_ROLE_ID)
            if role and role not in member.roles:
                bot.loop.call_soon_threadsafe(lambda: bot.loop.create_task(member.add_roles(role)))

    return '認証が完了しました！ウィンドウを閉じて大丈夫です。'

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

        staff_role = guild.get_role(STAFF_ROLE_ID) if STAFF_ROLE_ID != 0 else None
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

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
# 3. 自販機システム（スプレッドシート連携版）
# -------------------------------------------------------------
class ItemAddModal(discord.ui.Modal):
    def __init__(self, machine_name: str):
        super().__init__(title=f"自販機追加: {machine_name}")
        self.machine_name = machine_name

    item_name = discord.ui.TextInput(label="商品名", placeholder="例: 自動代行ソースコード", max_length=100)
    item_price = discord.ui.TextInput(label="価格（半角数字のみ）", placeholder="例: 1700", max_length=10)
    item_stock = discord.ui.TextInput(label="在庫数（半角数字 または ∞）", placeholder="例: 5 または ∞", max_length=10, default="∞")
    item_desc = discord.ui.TextInput(label="商品説明（誰でも見えます）", style=discord.TextStyle.paragraph, placeholder="例: 自動代行ソースコードです。", max_length=500)
    item_content = discord.ui.TextInput(label="商品の中身（購入者にだけ送られます）", style=discord.TextStyle.paragraph, placeholder="例: GoogleドライブのURLやシリアルコード", max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_admin_role(interaction.user):
            await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
            return

        try:
            price = int(self.item_price.value)
        except ValueError:
            await interaction.response.send_message("❌ 価格は半角数字で入力してください。", ephemeral=True)
            return

        stock_val = self.item_stock.value.strip()
        if stock_val in ["∞", "inf", "Infinity", "infinity"]:
            stock = 99
        else:
            try:
                stock = int(stock_val)
            except ValueError:
                await interaction.response.send_message("❌ 在庫数は数字または「∞」で入力してください。", ephemeral=True)
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
            "price": price,
            "stock": stock,
            "sold": 0
        }
        save_vending(data)
        
        await interaction.response.send_message(f"✅ 自販機「**{self.machine_name}**」に商品を追加しました！ (ID: `{new_id}`)", ephemeral=True)

class PurchaseConfirmView(discord.ui.View):
    def __init__(self, machine_name: str, item_id: str, item_data: dict):
        super().__init__(timeout=180)
        self.machine_name = machine_name
        self.item_id = item_id
        self.item_data = item_data

    @discord.ui.button(label="購入確定", style=discord.ButtonStyle.green, custom_id="confirm_purchase_btn")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_vending()
        if self.machine_name not in data or self.item_id not in data[self.machine_name]:
            await interaction.response.send_message("❌ この商品は存在しません。", ephemeral=True)
            return

        item = data[self.machine_name][self.item_id]
        current_stock = item.get("stock", 99)
        if current_stock != 99 and current_stock <= 0:
            await interaction.response.send_message("❌ 申し訳ありません。この商品は売り切れ（在庫切れ）です。", ephemeral=True)
            return

        item["sold"] += 1
        if current_stock != 99:
            item["stock"] -= 1
        save_vending(data)

        secret_content = item.get('content', '（中身が設定されていません）')
        
        embed = discord.Embed(
            title="✅ 購入が完了しました！",
            description=f"自販機: **{self.machine_name}**\n商品: **{item['name']}**\n金額: **{item['price']}円**",
            color=0x00ff00
        )
        embed.add_field(name="📦 あなたの商品の中身", value=secret_content, inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)

        if LOG_CHANNEL_ID != 0:
            log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🎉 取引実績・購入通知",
                    description=f"**自販機**: {self.machine_name}\n**購入者**: {interaction.user.mention}\n**商品名**: {item['name']}\n**価格**: {item['price']}円",
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
                    description=f"価格: {info['price']}円 | 在庫: {stock_str}個"
                ))
        super().__init__(placeholder="商品を選択してください", min_values=1, max_values=1, options=options, custom_id=f"vending_select_{machine_name}")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ 商品がありません。", ephemeral=True)
            return

        data = load_vending()
        items = data.get(self.machine_name, {})
        item_id = self.values[0]
        if item_id in items:
            item = items[item_id]
            stock_val = item.get('stock', 99)
            if stock_val != 99 and stock_val <= 0:
                await interaction.response.send_message("❌ この商品は現在売り切れです。", ephemeral=True)
                return

            embed = discord.Embed(title=f"購入確認 ({self.machine_name})", color=0x2b2d31)
            embed.add_field(name="商品名", value=item["name"], inline=False)
            embed.add_field(name="金額", value=f"{item['price']}円", inline=False)
            embed.add_field(name="商品説明", value=item["desc"], inline=False)
            view = PurchaseConfirmView(self.machine_name, item_id, item)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class VendingMainView(discord.ui.View):
    def __init__(self, machine_name: str):
        super().__init__(timeout=None)
        self.machine_name = machine_name

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.green, custom_id="vending_buy_main_btn")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        view.add_item(VendingSelect(self.machine_name))
        await interaction.response.send_message("セレクトメニューから商品を選択してください。", view=view, ephemeral=True)

    @discord.ui.button(label="🔍 在庫・販売数の確認", style=discord.ButtonStyle.blurple, custom_id="vending_stock_main_btn")
    async def stock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = load_vending()
        items = data.get(self.machine_name, {})
        embed = discord.Embed(title=f"📦 在庫・販売数チェッカー ({self.machine_name})", color=0x3498DB)
        if not items:
            embed.description = "現在登録されている商品はありません。"
        for i_id, info in items.items():
            stock_val = info.get('stock', 99)
            stock_str = "∞" if stock_val == 99 else str(stock_val)
            embed.add_field(name=info["name"], value=f"価格: {info['price']}円 | 在庫: {stock_str}個 | 販売数: {info.get('sold', 0)}個", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="➕ 商品を追加する", style=discord.ButtonStyle.gray, custom_id="vending_add_modal_btn")
    async def add_item_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_admin_role(interaction.user):
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

    if not auto_backup_task.is_running():
        auto_backup_task.start()
    
    config = load_announce_config()
    hours = config.get("interval_hours", 24)
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
# スラッシュコマンド
# -------------------------------------------------------------
@bot.tree.command(name="setup_vending", description="自販機パネルを送信します（名前で個別識別できます）。")
@app_commands.describe(machine_name="この自販機の名前（識別名。例: メイン自販機など）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_vending(interaction: discord.Interaction, machine_name: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    data = load_vending()
    if machine_name not in data:
        data[machine_name] = {}
        save_vending(data)

    items = data[machine_name]
    vending_embed = discord.Embed(title=f"🎪 自販機: {machine_name}", description="以下のメニューまたはボタンから商品をご購入いただけます。", color=0x2b2d31)
    if not items:
        vending_embed.add_field(name="お知らせ", value="現在販売中の商品はございません。", inline=False)
    else:
        for i_id, info in items.items():
            field_value = f"商品説明: {info.get('desc', 'なし')}\n\n値段: **{info['price']}円**"
            vending_embed.add_field(name=info["name"], value=field_value, inline=False)
            
    await interaction.channel.send(embed=vending_embed, view=VendingMainView(machine_name))
    await interaction.response.send_message(f"✅ 自販機パネル（識別名: **{machine_name}**）を設置しました！", ephemeral=True)

@bot.tree.command(name="vending_list", description="指定した自販機の登録商品一覧を確認します。")
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
        embed.add_field(name=f"ID: {i_id}", value=f"商品名: {info['name']} | 価格: {info['price']}円 | 在庫: {stock_str}個", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="vending_delete", description="指定した自販機から特定の商品を削除します。")
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

# -------------------------------------------------------------
# 起動処理
# -------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    bot.run(os.environ['DISCORD_BOT_TOKEN'])

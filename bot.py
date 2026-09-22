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

# -------------------------------------------------------------
# ⚙️ 設定（Renderなどの環境変数から安全に読み込みます）
# -------------------------------------------------------------
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", 0))

# ロールIDで管理するように変更
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
VENDING_FILE = "vending_items.json"
ANNOUNCE_CONFIG_FILE = "announce_config.json"
BACKUP_DIR = "backups"

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

def load_vending():
    if not os.path.exists(VENDING_FILE):
        default_items = {}
        save_vending(default_items)
        return default_items
    try:
        with open(VENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_vending(data):
    with open(VENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_announce_config():
    if not os.path.exists(ANNOUNCE_CONFIG_FILE):
        default_config = {
            "channel_id": 0,
            "interval_hours": 24,
            "message": "ショップは24時間稼働中です。\nご用件やチケット作成はチャンネル内のパネルからどうぞ！"
        }
        save_announce_config(default_config)
        return default_config
    try:
        with open(ANNOUNCE_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"channel_id": 0, "interval_hours": 24, "message": "ショップ稼働中！"}

def save_announce_config(config):
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
    if os.path.exists(VENDING_FILE):
        shutil.copy(VENDING_FILE, os.path.join(BACKUP_DIR, f"vending_items_backup_{timestamp}.json"))

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
        return '''
        <html>
            <head><meta charset="utf-8"><title>認証キャンセル</title></head>
            <body style="background-color: #1a1b1e; color: #fff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                <div style="background: #2b2d31; padding: 40px; border-radius: 12px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.3); max-width: 400px;">
                    <h2 style="color: #ed4245; margin-bottom: 10px;">❌ 認証がキャンセルされました</h2>
                    <p style="color: #949ba4; font-size: 14px;">ウィンドウを閉じてDiscordに戻ってください。</p>
                </div>
            </body>
        </html>
        '''

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
        return f'''
        <html>
            <head><meta charset="utf-8"><title>認証エラー</title></head>
            <body style="background-color: #1a1b1e; color: #fff; font-family: sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0;">
                <div style="background: #2b2d31; padding: 40px; border-radius: 12px; text-align: center; box-shadow: 0 4px 15px rgba(0,0,0,0.3); max-width: 400px;">
                    <h2 style="color: #ed4245; margin-bottom: 10px;">⚠️ エラーが発生しました</h2>
                    <p style="color: #949ba4; font-size: 14px;">トークンの取得に失敗しました。</p>
                </div>
            </body>
        </html>
        '''

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

    return f'''
    <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>認証成功</title>
            <style>
                body {{
                    background-color: #1a1b1e;
                    color: #dbdee1;
                    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    height: 100vh;
                    margin: 0;
                }}
                .card {{
                    background: #2b2d31;
                    padding: 40px 30px;
                    border-radius: 16px;
                    text-align: center;
                    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
                    max-width: 380px;
                    width: 90%;
                    border: 1px solid #3f4147;
                }}
                .icon-container {{
                    width: 70px;
                    height: 70px;
                    background: rgba(87, 242, 135, 0.1);
                    border-radius: 50%;
                    display: flex;
                    justify-content: center;
                    align-items: center;
                    margin: 0 auto 20px auto;
                }}
                .checkmark {{
                    font-size: 36px;
                    color: #57F287;
                }}
                h1 {{
                    color: #f2f3f5;
                    font-size: 22px;
                    margin: 0 0 10px 0;
                }}
                .username {{
                    color: #5865F2;
                    font-weight: 600;
                }}
                p {{
                    color: #949ba4;
                    font-size: 14px;
                    line-height: 1.5;
                    margin: 0 0 25px 0;
                }}
                .badge {{
                    display: inline-block;
                    background: #313338;
                    color: #57F287;
                    padding: 8px 16px;
                    border-radius: 20px;
                    font-size: 13px;
                    font-weight: 500;
                    border: 1px solid #57F287;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="icon-container">
                    <div class="checkmark">✓</div>
                </div>
                <h1>認証が完了しました！</h1>
                <p>ようこそ、<span class="username">{username}</span> さん！<br>ロールの付与が正常に行われました。</p>
                <div class="badge">✨ このウィンドウを閉じて大丈夫です</div>
            </div>
        </body>
    </html>
    '''

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

# -------------------------------------------------------------
# 2. チケット機能（オーナー・スタッフのメンション追加）
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
# 3. 自販機システム
# -------------------------------------------------------------
class ItemAddModal(discord.ui.Modal, title="自販機への商品追加"):
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

        items = load_vending()
        new_id = str(max([int(k) for k in items.keys()] + [0]) + 1)
        
        items[new_id] = {
            "name": self.item_name.value,
            "desc": self.item_desc.value,
            "content": self.item_content.value,
            "price": price,
            "stock": stock,
            "sold": 0
        }
        save_vending(items)
        perform_backup()
        
        await interaction.response.send_message(f"✅ 商品を追加しました！ (ID: `{new_id}`)", ephemeral=True)

class PurchaseConfirmView(discord.ui.View):
    def __init__(self, item_id, item_data):
        super().__init__(timeout=180)
        self.item_id = item_id
        self.item_data = item_data

    @discord.ui.button(label="購入確定", style=discord.ButtonStyle.green, custom_id="confirm_purchase_btn")
    async def confirm_purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = load_vending()
        if self.item_id not in items:
            await interaction.response.send_message("❌ この商品は存在しません。", ephemeral=True)
            return

        current_stock = items[self.item_id].get("stock", 99)
        if current_stock != 99 and current_stock <= 0:
            await interaction.response.send_message("❌ 申し訳ありません。この商品は売り切れ（在庫切れ）です。", ephemeral=True)
            return

        items[self.item_id]["sold"] += 1
        if current_stock != 99:
            items[self.item_id]["stock"] -= 1
        save_vending(items)
        perform_backup()

        secret_content = self.item_data.get('content', '（中身が設定されていません）')
        
        embed = discord.Embed(
            title="✅ 購入が完了しました！",
            description=f"商品: **{self.item_data['name']}**\n金額: **{self.item_data['price']}円**",
            color=0x00ff00
        )
        embed.add_field(name="📦 あなたの商品の中身（この内容はあなただけに表示されています）", value=secret_content, inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)

        if LOG_CHANNEL_ID != 0:
            log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="🎉 取引実績・購入通知",
                    description=f"**購入者**: {interaction.user.mention}\n**商品名**: {self.item_data['name']}\n**価格**: {self.item_data['price']}円",
                    color=0x3498DB,
                    timestamp=datetime.now()
                )
                await log_channel.send(embed=log_embed)
                await update_achievement_channel_name(interaction.guild)

class VendingSelect(discord.ui.Select):
    def __init__(self):
        items = load_vending()
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
        super().__init__(placeholder="商品を選択してください", min_values=1, max_values=1, options=options, custom_id="vending_select_menu")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            await interaction.response.send_message("❌ 商品がありません。", ephemeral=True)
            return

        items = load_vending()
        item_id = self.values[0]
        if item_id in items:
            item = items[item_id]
            stock_val = item.get('stock', 99)
            if stock_val != 99 and stock_val <= 0:
                await interaction.response.send_message("❌ この商品は現在売り切れです。", ephemeral=True)
                return

            embed = discord.Embed(title="購入確認", color=0x2b2d31)
            embed.add_field(name="商品名", value=item["name"], inline=False)
            embed.add_field(name="金額", value=f"{item['price']}円", inline=False)
            embed.add_field(name="商品説明", value=item["desc"], inline=False)
            view = PurchaseConfirmView(item_id, item)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class VendingMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛒 購入する", style=discord.ButtonStyle.green, custom_id="vending_buy_main_btn")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = discord.ui.View()
        view.add_item(VendingSelect())
        await interaction.response.send_message("セレクトメニューから商品を選択してください。", view=view, ephemeral=True)

    @discord.ui.button(label="🔍 在庫・販売数の確認", style=discord.ButtonStyle.blurple, custom_id="vending_stock_main_btn")
    async def stock_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        items = load_vending()
        embed = discord.Embed(title="📦 在庫・販売数チェッカー", color=0x3498DB)
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
        await interaction.response.send_modal(ItemAddModal())

class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        base_url = RENDER_EXTERNAL_URL if RENDER_EXTERNAL_URL else "http://localhost:8080"
        oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={requests.utils.quote(base_url + '/callback')}&response_type=code&scope=identify%20guilds.join"
        self.add_item(discord.ui.Button(label="✅ 認証してロールを受け取る", style=discord.ButtonStyle.link, url=oauth_url))

# -------------------------------------------------------------
# ボット起動イベント（スラッシュコマンド同期処理を含む）
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
    bot.add_view(VendingMainView())
    
    for guild in bot.guilds:
        await update_achievement_channel_name(guild)

# -------------------------------------------------------------
# スラッシュコマンド（管理者専用）
# -------------------------------------------------------------
@bot.tree.command(name="recreate", description="現在のチャンネルを削除し、同じ設定で新しく作り直します。")
@app_commands.checks.has_permissions(administrator=True)
async def recreate_channel(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    channel = interaction.channel
    guild = interaction.guild
    
    name = channel.name
    category = channel.category
    overwrites = channel.overwrites
    position = channel.position
    topic = channel.topic
    slowmode = channel.slowmode_delay
    nsfw = channel.is_nsfw()
    
    await interaction.response.send_message("✨ チャンネルをリフレッシュしています...", ephemeral=True)
    
    new_channel = await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites,
        position=position,
        topic=topic,
        slowmode_delay=slowmode,
        nsfw=nsfw,
        reason=f"{interaction.user} によってチャンネルがリフレッシュ（再作成）されました"
    )
    
    await channel.delete(reason="リフレッシュ用の再作成")
    await new_channel.send(f"✨ チャンネルをリフレッシュ（作り直し）しました！", delete_after=5)

@bot.tree.command(name="panel_embed", description="埋め込みパネルを送信します。")
@app_commands.describe(title="パネルのタイトル", description="パネルに表示する本文")
@app_commands.checks.has_permissions(administrator=True)
async def panel_embed(interaction: discord.Interaction, title: str, description: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x2b2d31
    )
    embed.set_footer(text=interaction.guild.name, icon_url=interaction.guild.icon.url if interaction.guild.icon else None)
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ パネルを送信しました！", ephemeral=True)

@bot.tree.command(name="update_achievement", description="実績チャンネルのカウントを手動で同期します。")
@app_commands.checks.has_permissions(administrator=True)
async def update_achievement(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    await update_achievement_channel_name(interaction.guild)
    await interaction.response.send_message("✅ 実績チャンネルのカウントを更新しました！", ephemeral=True)

@bot.tree.command(name="set_announce_channel", description="定期送信のチャンネルを設定します。")
@app_commands.describe(channel="送信先のテキストチャンネル")
@app_commands.checks.has_permissions(administrator=True)
async def set_announce_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    config = load_announce_config()
    config["channel_id"] = channel.id
    save_announce_config(config)
    await interaction.response.send_message(f"✅ 定期送信のチャンネルを {channel.mention} に設定しました！", ephemeral=True)

@bot.tree.command(name="set_announce_interval", description="定期送信の間隔（時間）を変更します。")
@app_commands.describe(hours="何時間おきに送信するか（1以上）")
@app_commands.checks.has_permissions(administrator=True)
async def set_announce_interval(interaction: discord.Interaction, hours: int):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    if hours < 1:
        await interaction.response.send_message("❌ 1時間以上の数値を指定してください。", ephemeral=True)
        return
    config = load_announce_config()
    config["interval_hours"] = hours
    save_announce_config(config)
    scheduled_announcement.change_interval(hours=hours)
    await interaction.response.send_message(f"✅ 定期送信の間隔を **{hours}時間おき** に変更しました！", ephemeral=True)

@bot.tree.command(name="set_announce_text", description="定期送信のメッセージを変更します。")
@app_commands.describe(text="送信するメッセージ内容")
@app_commands.checks.has_permissions(administrator=True)
async def set_announce_text(interaction: discord.Interaction, text: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    config = load_announce_config()
    config["message"] = text
    save_announce_config(config)
    await interaction.response.send_message(f"✅ 定期送信のメッセージを更新しました！", ephemeral=True)

@bot.tree.command(name="announce_status", description="定期送信の設定状況を確認します。")
@app_commands.checks.has_permissions(administrator=True)
async def announce_status(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    config = load_announce_config()
    ch = bot.get_channel(config.get("channel_id", 0))
    ch_str = ch.mention if ch else "未設定 (0)"
    
    embed = discord.Embed(title="📢 定期送信の設定状況", color=0x3498DB)
    embed.add_field(name="送信先チャンネル", value=ch_str, inline=False)
    embed.add_field(name="送信間隔", value=f"{config.get('interval_hours', 24)}時間おき", inline=False)
    embed.add_field(name="送信メッセージ", value=config.get("message", "なし"), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="announce_test", description="定期送信のテストメッセージを今すぐ送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def announce_test(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    config = load_announce_config()
    embed = discord.Embed(
        title="📢 【テスト送信】ショップ稼働中！",
        description=config.get("message", "ショップ稼働中！"),
        color=0xF1C40F
    )
    await interaction.channel.send(embed=embed)
    await interaction.response.send_message("✅ テストメッセージを送信しました！", ephemeral=True)

@bot.tree.command(name="call_users", description="認証済みユーザーをサーバーに強制参加させます。")
@app_commands.checks.has_permissions(administrator=True)
async def call_users(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return

    db = load_data()
    if not db:
        await interaction.response.send_message("📭 認証されたユーザーデータがありません。", ephemeral=True)
        return

    guild = interaction.guild
    success_count = 0
    fail_count = 0

    await interaction.response.send_message(f"🔄 認証済みユーザーの強制参加（コール）を開始します... (対象: {len(db)}人)", ephemeral=True)

    for user_id, info in db.items():
        access_token = info.get("access_token")
        if not access_token:
            fail_count += 1
            continue

        url = f"https://discord.com/api/v10/guilds/{guild.id}/members/{user_id}"
        headers = {
            "Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
            "Content-Type": "application/json"
        }
        payload = {"access_token": access_token}
        
        response = requests.put(url, headers=headers, json=payload)
        if response.status_code in [201, 204]:
            success_count += 1
        else:
            fail_count += 1
        
        time.sleep(0.5)

    await interaction.followup.send(f"✅ コール処理が完了しました！\n🎯 成功: {success_count}人\n❌ 失敗: {fail_count}人", ephemeral=True)

@bot.tree.command(name="help_cmd", description="すべての管理・操作コマンド一覧を表示します。")
@app_commands.checks.has_permissions(administrator=True)
async def help_cmd(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return

    embed = discord.Embed(title="🤖 管理・操作コマンド一覧（スラッシュ対応）", color=0xF1C40F)
    embed.add_field(name="パネル設置系", value="`/setup_verify` : 認証パネルを設置\n`/setup_ticket` : チケットパネルを設置\n`/setup_vending` : 自販機パネルを設置", inline=False)
    embed.add_field(name="自販機管理系", value="`/vending_list` : 登録商品とIDの一覧確認\n`/vending_delete` : 指定したIDの自販機商品を削除", inline=False)
    embed.add_field(name="お知らせ・定期送信系", value="`/set_announce_channel` : 送信先を設定\n`/set_announce_interval` : 間隔(時間)を変更\n`/set_announce_text` : メッセージを変更\n`/announce_status` : 設定状況の確認\n`/announce_test` : テスト送信", inline=False)
    embed.add_field(name="サーバー管理・ユーティリティ", value="`/recreate` : チャンネルリフレッシュ\n`/panel_embed` : 埋め込みパネル送信\n`/update_achievement` : 実績カウント同期\n`/call_users` : 認証済みユーザー強制参加\n`/manual_backup` : 手動バックアップ", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="manual_backup", description="手動バックアップを実行します。")
@app_commands.checks.has_permissions(administrator=True)
async def manual_backup(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    perform_backup()
    await interaction.response.send_message("📦 手動バックアップが完了しました！", ephemeral=True)

@bot.tree.command(name="setup_verify", description="サーバー認証パネルを送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_verify(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    await interaction.channel.send(embed=discord.Embed(title="📜 サーバー認証", description="下のボタンから認証してください。", color=0x5865F2), view=VerifyView())
    await interaction.response.send_message("✅ 認証パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="setup_ticket", description="お問い合わせチケットパネルを送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_ticket(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    await interaction.channel.send(embed=discord.Embed(title="🎫 お問い合わせ", description="チケットを作成するには下のボタンを押してください。", color=0x57F287), view=TicketView())
    await interaction.response.send_message("✅ チケットパネルを設置しました！", ephemeral=True)

@bot.tree.command(name="setup_vending", description="自販機パネルを送信します。")
@app_commands.checks.has_permissions(administrator=True)
async def setup_vending(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    
    items = load_vending()
    vending_embed = discord.Embed(title="🎪 自販機", description="以下のメニューまたはボタンから商品をご購入いただけます。", color=0x2b2d31)
    if not items:
        vending_embed.add_field(name="お知らせ", value="現在販売中の商品はございません。", inline=False)
    else:
        for i_id, info in items.items():
            field_value = f"商品説明: {info.get('desc', 'なし')}\n\n値段: **{info['price']}円**"
            vending_embed.add_field(name=info["name"], value=field_value, inline=False)
            
    await interaction.channel.send(embed=vending_embed, view=VendingMainView())
    await interaction.response.send_message("✅ 自販機パネルを設置しました！", ephemeral=True)

@bot.tree.command(name="vending_list", description="登録されている自販機商品の一覧とIDを確認します。")
@app_commands.checks.has_permissions(administrator=True)
async def vending_list(interaction: discord.Interaction):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    items = load_vending()
    embed = discord.Embed(title="🎪 登録商品一覧", color=0x3498DB)
    if not items:
        embed.description = "商品が登録されていません。"
    for i_id, info in items.items():
        stock_val = info.get('stock', 99)
        stock_str = "∞" if stock_val == 99 else str(stock_val)
        embed.add_field(name=f"ID: {i_id}", value=f"商品名: {info['name']} | 価格: {info['price']}円 | 在庫: {stock_str}個", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="vending_delete", description="指定したIDの自販機商品を削除します。")
@app_commands.describe(item_id="削除する商品のID (例: 1)")
@app_commands.checks.has_permissions(administrator=True)
async def vending_delete(interaction: discord.Interaction, item_id: str):
    if not has_admin_role(interaction.user):
        await interaction.response.send_message("❌ 管理者権限が必要です。", ephemeral=True)
        return
    items = load_vending()
    if item_id in items:
        deleted_name = items[item_id]["name"]
        del items[item_id]
        save_vending(items)
        perform_backup()
        await interaction.response.send_message(f"✅ 商品「{deleted_name}」(ID: {item_id}) を削除しました！", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ 指定されたID (ID: {item_id}) の商品は見つかりませんでした。`/vending_list` でIDを確認してください。", ephemeral=True)

# -------------------------------------------------------------
# 起動処理
# -------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    bot.run(os.environ['DISCORD_BOT_TOKEN'])

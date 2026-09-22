import discord
from discord.ext import commands, tasks
import json
import os
import shutil
import threading
import time
from datetime import datetime
from flask import Flask, request
import requests

# -------------------------------------------------------------
# ⚙️ 設定（すべてReplitのSecretsから安全に読み込みます）
# -------------------------------------------------------------
CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET")
GUILD_ID = int(os.environ.get("DISCORD_GUILD_ID", 0))

MEMBER_ROLE_NAME = os.environ.get("MEMBER_ROLE_NAME", "Member")
STAFF_ROLE_NAME = os.environ.get("STAFF_ROLE_NAME", "Staff")
ADMIN_ROLE_NAME = os.environ.get("ADMIN_ROLE_NAME", "Admin")

LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0))

REPL_SLUG = os.environ.get("REPL_SLUG")
REPL_OWNER = os.environ.get("REPL_OWNER")
if REPL_SLUG and REPL_OWNER:
    REDIRECT_URI = f"https://{REPL_SLUG}.{REPL_OWNER}.repl.co/callback"
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
        default_items = {
            "1": {"name": "ツムツム自動代行ソースコード", "desc": "ツムツムの自動代行ソースコードです", "price": 1700, "stock": 99, "sold": 0},
            "2": {"name": "保留対策済み全自動自販機", "desc": "支払い方法paypay&kyash対応", "price": 500, "stock": 10, "sold": 5},
            "3": {"name": "にゃんこ大戦争自動代行", "desc": "自動で代行を進めます", "price": 200, "stock": 10, "sold": 7}
        }
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
            "message": "ソースコード自販機は24時間稼働中です。\nご用件やチケット作成はチャンネル内のパネルからどうぞ！"
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
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)

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
    oauth_url = f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join"
    return f'''
    <html>
        <head><title>サーバー認証</title></head>
        <body style="text-align: center; font-family: sans-serif; margin-top: 50px;">
            <h2>サーバーへようこそ！</h2>
            <p>下のボタンを押して認証を完了させると、自動でロールが与えられます。</p>
            <a href="{oauth_url}" style="background-color: #5865F2; color: white; padding: 15px 25px; text-decoration: none; border-radius: 5px; font-size: 18px;">👉 認証を完了する</a>
        </body>
    </html>
    '''

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "認証がキャンセルされました。"

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
        return f"トークンの取得に失敗しました: {json_res}"

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
            role = discord.utils.get(guild.roles, name=MEMBER_ROLE_NAME)
            if role and role not in member.roles:
                bot.loop.call_soon_threadsafe(lambda: bot.loop.create_task(member.add_roles(role)))

    return f"<h2>認証成功！ {username} さん、ありがとうございます。ロールが付与されました。ウィンドウを閉じて大丈夫です。</h2>"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

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

        staff_role = discord.utils.get(guild.roles, name=STAFF_ROLE_NAME)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, view_channel=True)

        channel_name = f"ticket-{interaction.user.name.lower()}"
        existing_channel = discord.utils.get(category.text_channels, name=channel_name)
        
        if existing_channel:
            await interaction.response.send_message(f"❌ すでにオープンしているチケットがあります: {existing_channel.mention}", ephemeral=True)
            return

        channel = await guild.create_text_channel(channel_name, category=category, overwrites=overwrites)
        close_view = TicketCloseView()
        
        embed = discord.Embed(
            title="❄️ ぷに代行へようこそ！",
            description=f"ここがプライベートチャンネル {channel.mention} の始まりです。\n\n{interaction.user.mention}さん、ようこそ！スタッフが対応いたしますので少々お待ちください。",
            color=0x2b2d31
        )
        embed.add_field(name="📌 ご利用の流れ", value="1. スタッフからの案内に従う\n2. 詳細を伝える", inline=False)
        await channel.send(embed=embed, view=close_view)
        await interaction.response.send_message(f"✅ チケットを作成しました！ 👉 {channel.mention}", ephemeral=True)

# -------------------------------------------------------------
# 3. 自販機システム & 購入完了・実績自動カウント
# -------------------------------------------------------------
class ItemAddModal(discord.ui.Modal, title="自販機への商品追加"):
    item_name = discord.ui.TextInput(label="商品名", placeholder="例: ツムツム自動代行ソースコード", max_length=100)
    item_price = discord.ui.TextInput(label="価格（半角数字のみ）", placeholder="例: 1700", max_length=10)
    item_desc = discord.ui.TextInput(label="商品説明", style=discord.TextStyle.paragraph, placeholder="例: ツムツムの自動代行ソースコードです。", max_length=500)

    async def on_submit(self, interaction: discord.Interaction):
        if not has_admin_role(interaction.user):
            await interaction.response.send_message("❌ 権限がありません。", ephemeral=True)
            return

        try:
            price = int(self.item_price.value)
        except ValueError:
            await interaction.response.send_message("❌ 価格は半角数字で入力してください。", ephemeral=True)
            return

        items = load_vending()
        new_id = str(max([int(k) for k in items.keys()] + [0]) + 1)
        
        items[new_id] = {
            "name": self.item_name.value,
            "desc": self.item_desc.value,
            "price": price,
            "stock": 99,
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
        if self.item_id in items:
            items[self.item_id]["sold"] += 1
            if items[self.item_id]["stock"] != 99 and items[self.item_id]["stock"] > 0:
                items[self.item_id]["stock"] -= 1
            save_vending(items)
            perform_backup()

        embed = discord.Embed(
            title="✅ 購入が完了しました",
            description=f"商品: **{self.item_data['name']}**\n金額: **{self.item_data['price']}円**",
            color=0x00ff00
        )
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
                stock_str = "∞" if info.get('stock', 99) == 99 else str(info.get('stock', 0))
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
            embed = discord.Embed(title="購入確認", color=0x2b2d31)
            embed.add_field(name="商品名", value=item["name"], inline=False)
            embed.add_field(name="金額", value=f"{item['price']}円", inline=False)
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
        for i_id, info in items.items():
            stock_str = "∞" if info.get('stock', 99) == 99 else str(info.get('stock', 0))
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

    @discord.ui.button(label="✅ Web認証・ロール受け取り", style=discord.ButtonStyle.link, url=f"https://{REPL_SLUG}.{REPL_OWNER}.repl.co/")
    async def verify_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

# -------------------------------------------------------------
# ボット起動イベント
# -------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"ログインしました: {bot.user.name} (ID: {bot.user.id})")
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
# 管理者コマンド
# -------------------------------------------------------------
@bot.command(name="recreate")
async def recreate_channel(ctx):
    """今いるチャンネルを一度削除し、同じ名前・同じ場所・同じ権限で新しく作り直します（リフレッシュ）"""
    if not has_admin_role(ctx.author):
        return
    
    channel = ctx.channel
    guild = ctx.guild
    
    # 現在の設定を保存
    name = channel.name
    category = channel.category
    overwrites = channel.overwrites
    position = channel.position
    topic = channel.topic
    slowmode = channel.slowmode_delay
    nsfw = channel.is_nsfw()
    
    # 新しいチャンネルを同じ場所に作成
    new_channel = await guild.create_text_channel(
        name=name,
        category=category,
        overwrites=overwrites,
        position=position,
        topic=topic,
        slowmode_delay=slowmode,
        nsfw=nsfw,
        reason=f"{ctx.author} によってチャンネルがリフレッシュ（再作成）されました"
    )
    
    # 旧チャンネルを削除
    await channel.delete(reason="リフレッシュ用の再作成")
    
    # 新チャンネルに完了メッセージ（数秒で消える）を送信
    await new_channel.send(f"✨ チャンネルをリフレッシュ（作り直し）しました！", delete_after=5)

@bot.command(name="panel_embed")
async def panel_embed(ctx, title: str, *, description: str):
    """画像のような埋め込みパネルを送信します（例: !panel_embed タイトル | 説明文）"""
    if not has_admin_role(ctx.author):
        return
    await ctx.message.delete()
    
    embed = discord.Embed(
        title=title,
        description=description,
        color=0x2b2d31
    )
    embed.set_footer(text=ctx.guild.name, icon_url=ctx.guild.icon.url if ctx.guild.icon else None)
    await ctx.send(embed=embed)

@bot.command(name="update_achievement")
async def update_achievement(ctx):
    """手動で実績チャンネルのカウントを最新に同期します"""
    if not has_admin_role(ctx.author):
        return
    await update_achievement_channel_name(ctx.guild)
    await ctx.send("✅ 実績チャンネルのカウントを更新しました！", ephemeral=True)

@bot.command(name="set_announce_channel")
async def set_announce_channel(ctx, channel: discord.TextChannel):
    if not has_admin_role(ctx.author):
        return
    config = load_announce_config()
    config["channel_id"] = channel.id
    save_announce_config(config)
    await ctx.send(f"✅ 定期送信のチャンネルを {channel.mention} に設定しました！", ephemeral=True)

@bot.command(name="set_announce_interval")
async def set_announce_interval(ctx, hours: int):
    if not has_admin_role(ctx.author):
        return
    if hours < 1:
        await ctx.send("❌ 1時間以上の数値を指定してください。", ephemeral=True)
        return
    config = load_announce_config()
    config["interval_hours"] = hours
    save_announce_config(config)
    scheduled_announcement.change_interval(hours=hours)
    await ctx.send(f"✅ 定期送信の間隔を **{hours}時間おき** に変更しました！", ephemeral=True)

@bot.command(name="set_announce_text")
async def set_announce_text(ctx, *, text: str):
    if not has_admin_role(ctx.author):
        return
    config = load_announce_config()
    config["message"] = text
    save_announce_config(config)
    await ctx.send(f"✅ 定期送信のメッセージを更新しました！", ephemeral=True)

@bot.command(name="announce_status")
async def announce_status(ctx):
    if not has_admin_role(ctx.author):
        return
    config = load_announce_config()
    ch = bot.get_channel(config.get("channel_id", 0))
    ch_str = ch.mention if ch else "未設定 (0)"
    
    embed = discord.Embed(title="📢 定期送信の設定状況", color=0x3498DB)
    embed.add_field(name="送信先チャンネル", value=ch_str, inline=False)
    embed.add_field(name="送信間隔", value=f"{config.get('interval_hours', 24)}時間おき", inline=False)
    embed.add_field(name="送信メッセージ", value=config.get("message", "なし"), inline=False)
    await ctx.send(embed=embed, ephemeral=True)

@bot.command(name="announce_test")
async def announce_test(ctx):
    if not has_admin_role(ctx.author):
        return
    config = load_announce_config()
    embed = discord.Embed(
        title="📢 【テスト送信】ショップ稼働中！",
        description=config.get("message", "ショップ稼働中！"),
        color=0xF1C40F
    )
    await ctx.send(embed=embed)

@bot.command(name="call_users")
async def call_users(ctx):
    if not has_admin_role(ctx.author):
        return

    db = load_data()
    if not db:
        await ctx.send("📭 認証されたユーザーデータがありません。")
        return

    guild = ctx.guild
    success_count = 0
    fail_count = 0

    status_msg = await ctx.send(f"🔄 認証済みユーザーの強制参加（コール）を開始します... (対象: {len(db)}人)")

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

    await status_msg.content = f"✅ コール処理が完了しました！\n🎯 成功: {success_count}人\n❌ 失敗: {fail_count}人"
    await status_msg.edit(content=status_msg.content)

@bot.command(name="help_cmd")
async def help_cmd(ctx):
    if not has_admin_role(ctx.author):
        return

    embed = discord.Embed(title="🤖 管理コマンド一覧", color=0xF1C40F)
    embed.add_field(name="1. `!recreate`", value="現在のチャンネルを削除し、同じ設定で新しく作り直します（リフレッシュ）。", inline=False)
    embed.add_field(name="2. `!panel_embed [タイトル] [説明文]`", value="画像のような埋め込みパネルを送信します。", inline=False)
    embed.add_field(name="3. `!update_achievement`", value="実績チャンネルのカウント数を手動で最新に同期します。", inline=False)
    embed.add_field(name="4. `!setup_panels`", value="認証、チケット、自販機パネルを一括設置します。", inline=False)
    embed.add_field(name="5. `!set_announce_channel [#ch]`", value="定期送信チャンネルを設定します。", inline=False)
    embed.add_field(name="6. `!set_announce_interval [時間]`", value="定期送信の間隔を変更します。", inline=False)
    embed.add_field(name="7. `!set_announce_text [文章]`", value="定期送信の文章を変更します。", inline=False)
    embed.add_field(name="8. `!announce_status`", value="定期送信の設定を確認します。", inline=False)
    embed.add_field(name="9. `!announce_test`", value="定期送信のテスト投稿を行います。", inline=False)
    embed.add_field(name="10. `!call_users`", value="認証済みユーザーを強制参加させます。", inline=False)
    embed.add_field(name="11. `!manual_backup`", value="手動でバックアップを作成します。", inline=False)
    embed.add_field(name="12. `!vending_list`", value="登録商品一覧を確認します。", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def manual_backup(ctx):
    if not has_admin_role(ctx.author):
        return
    perform_backup()
    await ctx.send("📦 手動バックアップが完了しました！", ephemeral=True)

@bot.command()
async def setup_panels(ctx):
    if not has_admin_role(ctx.author):
        return
    await ctx.message.delete()
    
    await ctx.send(embed=discord.Embed(title="📜 サーバー認証", description="下のボタンから認証してください。", color=0x5865F2), view=VerifyView())
    await ctx.send(embed=discord.Embed(title="🎫 お問い合わせ", description="チケット作成", color=0x57F287), view=TicketView())
    
    items = load_vending()
    vending_embed = discord.Embed(title="🎪 ソースコード自販機", color=0x2b2d31)
    for i_id, info in items.items():
        vending_embed.add_field(name=info["name"], value=f"値段: {info['price']}円", inline=False)
    await ctx.send(embed=vending_embed, view=VendingMainView())
    await ctx.send("✨ パネルを設置しました！", delete_after=5)

@bot.command()
async def vending_list(ctx):
    if not has_admin_role(ctx.author):
        return
    items = load_vending()
    embed = discord.Embed(title="🎪 登録商品一覧", color=0x3498DB)
    for i_id, info in items.items():
        embed.add_field(name=id, value=f"ID: {i_id} | {info['name']}", inline=False) # Simplified for display
    await ctx.send(embed=embed)

# -------------------------------------------------------------
# 起動処理
# -------------------------------------------------------------
if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()

    bot.run(os.environ['DISCORD_BOT_TOKEN'])

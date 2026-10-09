import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import aiohttp
from urllib.parse import urljoin

load_dotenv()

NETFLIX_LOG_CHANNEL_ID = int(os.getenv("NETFLIX_PANEL_LOG_CHANNEL_ID", 0))
NETFLIX_VALIDATION_URL = os.getenv("NETFLIX_COOKIE_VALIDATION_URL", "https://www.netflix.com/login")
NETFLIX_LOGIN_REDIRECT = os.getenv("NETFLIX_LOGIN_REDIRECT_URL", "https://www.netflix.com/")

class NetflixModal(discord.ui.Modal, title="Netflix Cookie入力"):
    cookie = discord.ui.TextInput(
        label="Netflixクッキー (NfSession等)",
        style=discord.TextStyle.paragraph,
        placeholder='NfSession=xxxxxx; ...',
        required=True,
        max_length=4000
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)

        cookie_str = self.cookie.value.strip()
        if not cookie_str:
            await interaction.followup.send("❌ クッキーオブジェクトが空です", ephemeral=True)
            return

        # 検証
        validated, msg = await validate_netflix_cookie(cookie_str)
        if not validated:
            await interaction.followup.send(f"❌ 検証失敗: {msg}", ephemeral=True)
            return

        # ログインリンク生成
        login_url = f"{NETFLIX_LOGIN_REDIRECT}?cookie={cookie_str}"

        # ユーザーに返す
        embed = discord.Embed(
            title="✅ Netflix クッキー検証完了",
            description=f"[ログインリンク]({login_url})",
            color=discord.Color.green
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

        # ログチャンネルに記録
        if NETFLIX_LOG_CHANNEL_ID:
            log_channel = interaction.guild.get_channel(NETFLIX_LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="Netflix Cookie Entry",
                    description=f"User: {interaction.user}\nStatus: Success",
                    color=discord.Color.blue
                )
                await log_channel.send(embed=log_embed)

async def validate_netflix_cookie(cookie_str: str) -> tuple[bool, str]:
    headers = {"Cookie": cookie_str}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(NETFLIX_VALIDATION_URL, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    return True, "Valid"
                return False, f"HTTP {resp.status}"
    except Exception as e:
        return False, str(e)

class NetflixPanelView(discord.ui.View):
    @discord.ui.button(label="Netflix クッキー入力", style=discord.ButtonStyle.secondary, custom_id="netflix_cookie_button")
    async def netflix_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NetflixModal())

class NetflixPanel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="netflixパネル設置", description="Netflixクッキーパネルを設置")
    @app_commands.describe(channel="設置チャンネル (未指定で実行チャンネル)")
    async def setup_panel(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        embed = discord.Embed(
            title="🎬 Netflix クッキーパネル",
            description="下記ボタンを押してクッキーを入力してください\n"
                        "生成されるリンクでNetflixにログインできます",
            color=discord.Colour.red(）
        )
        await target.send(embed=embed, view=NetflixPanelView())
        await interaction.response.send_message("✅ パネルを設置しました", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        print(f"NetflixPanel Cog loaded")

async def setup(bot):
    await bot.add_cog(NetflixPanel(bot))

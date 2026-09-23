import discord
from discord import app_commands
from discord.ext import commands
import os
import json
import asyncio

# 外部モジュールのインポート
import paypayu
from utils import is_allowed

class PayPayCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # PayPayログインコマンド
    @app_commands.command(name="paypay_login", description="PayPayにログインします")
    @is_allowed()
    async def paypay_login(self, interaction: discord.Interaction, phone: str, password: str):
        await interaction.response.defer(ephemeral=True)
        try:
            # paypayuモジュールを使用してログイン処理を実行
            client = paypayu.PayPayAsync()
            result = await client.login(phone, password)
            
            # トークンの保存処理等
            await interaction.followup.send("PayPayへのログインに成功しました！", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"ログインエラー: {str(e)}", ephemeral=True)

    # PayPay設定確認コマンド
    @app_commands.command(name="paypay_status", description="PayPayの接続状態を確認します")
    @is_allowed()
    async def paypay_status(self, interaction: discord.Interaction):
        await interaction.response.send_message("PayPay機能は正常に読み込まれています。", ephemeral=True)

# BotへのCog登録関数
async def setup(bot: commands.Bot):
    await bot.add_cog(PayPayCog(bot))

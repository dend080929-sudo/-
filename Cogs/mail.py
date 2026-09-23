from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from mail_service import hide_address, issue_address


class MailPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MailIssueButton())


class MailIssueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="メールアドレスを発行",
            style=discord.ButtonStyle.primary,
            custom_id="mail_issue_button",
        )

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        address = issue_address(interaction.user.id)
        embed = discord.Embed(
            title="あなたのメールアドレス",
            description=f"```{address}```",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="受信方法",
            value="このアドレスに届いたメールは、設定された通常チャンネルの受信パネルに表示されます。",
            inline=False,
        )
        embed.set_footer(text="削除ボタンはDiscord上の表示だけを削除します。アドレスは停止しません。")
        await interaction.followup.send(
            embed=embed,
            view=MailAddressView(address),
            ephemeral=True,
        )


class MailAddressView(discord.ui.View):
    def __init__(self, address: str):
        super().__init__(timeout=None)
        self.add_item(MailHideButton(address))


class MailHideButton(discord.ui.Button):
    def __init__(self, address: str):
        super().__init__(
            label="表示を削除",
            style=discord.ButtonStyle.danger,
            custom_id=f"mail_hide:{address}",
        )
        self.address = address

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        if not hide_address(self.address, interaction.user.id):
            await interaction.followup.send("このメールアドレスを操作できません。", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
        await interaction.followup.send(
            "Discord上の表示を削除しました。メールアドレス自体は停止していません。\n"
            "削除後に届くメールは保管チャンネルへ送られます。",
            ephemeral=True,
        )


class MailCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="メールパネル設置", description="メールアドレス発行パネルを設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def mail_panel(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=False)
        embed = discord.Embed(
            title="✉️ メールアドレス発行",
            description=(
                "下のボタンから、あなた専用のメールアドレスを発行できます。\n"
                "発行されたアドレスに届いたメールは、設定された受信チャンネルに表示されます。"
            ),
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="削除について",
            value="削除ボタンはDiscord上の表示だけを削除します。アドレスは停止されず、削除後のメールは保管チャンネルへ送られます。",
            inline=False,
        )
        embed.set_footer(text="管理者がこのパネルを設置できます")
        await interaction.followup.send(embed=embed, view=MailPanelView())


async def setup(bot: commands.Bot):
    await bot.add_cog(MailCog(bot))

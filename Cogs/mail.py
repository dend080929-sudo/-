from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from mail_service import (
    get_current_address,
    hide_current_address,
    issue_address,
    save_panel_info,
)


def personal_embed(user_id: int) -> discord.Embed:
    address = get_current_address(user_id)
    shown = f"`{address}`" if address else "（未発行）"
    embed = discord.Embed(
        title="✉️ メールパネル",
        description="発行・削除・コピーをこのパネルから操作できます。",
        color=discord.Color.blue(),
    )
    embed.add_field(name="メールアドレス", value=shown, inline=False)
    embed.add_field(name="受信メール", value="新着メールはこのパネルの下に表示されます。", inline=False)
    embed.set_footer(text="削除してもメールアドレス自体は停止しません。削除後のメールは保管チャンネルへ送られます。")
    return embed


class MailPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MailIssueButton())
        self.add_item(MailDeleteButton())
        self.add_item(MailCopyButton())


class MailIssueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="メールアドレスを発行", style=discord.ButtonStyle.primary, custom_id="mail_issue_button")

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        address = issue_address(interaction.user.id)
        await interaction.message.edit(embed=personal_embed(interaction.user.id), view=MailPanelView())
        await interaction.followup.send(f"メールアドレスを発行しました: `{address}`", ephemeral=True)


class MailDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="削除", style=discord.ButtonStyle.danger, custom_id="mail_delete_button")

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        hide_current_address(interaction.user.id)
        await interaction.message.edit(embed=personal_embed(interaction.user.id), view=MailPanelView())


class MailCopyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="コピー", style=discord.ButtonStyle.secondary, custom_id="mail_copy_button")

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        address = get_current_address(interaction.user.id)
        text = f"コピーするメールアドレス:\n`{address}`" if address else "先に「メールアドレスを発行」を押してください。"
        await interaction.followup.send(text, ephemeral=True)


class MailCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="メールパネル設置", description="メール機能のパネルを設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def mail_panel(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=False)
        message = await interaction.followup.send(
            embed=personal_embed(interaction.user.id),
            view=MailPanelView(),
            wait=True,
        )
        save_panel_info(interaction.user.id, interaction.channel.id, message.id)


async def setup(bot: commands.Bot):
    await bot.add_cog(MailCog(bot))
    bot.add_view(MailPanelView())

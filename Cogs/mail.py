from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from mail_service import (
    clear_gmail_alias,
    delete_received_messages,
    get_current_address,
    get_gmail_info,
    hide_current_address,
    issue_address,
    issue_gmail_alias,
    register_gmail,
    save_panel_info,
    unregister_gmail,
)


def personal_embed(user_id: int) -> discord.Embed:
    address = get_current_address(user_id)
    mail_shown = f"`{address}`" if address else "（未発行）"
    gmail = get_gmail_info(user_id)
    registered_gmail = gmail.get("gmail") or "（未登録）"
    alias = gmail.get("current_alias") or "（未発行）"
    embed = discord.Embed(
        title="✉️ メール・Gmailパネル",
        description="メールアドレスとGmailエイリアスをこのパネルから操作できます。",
        color=discord.Color.blue(),
    )
    embed.add_field(name="メールアドレス", value=mail_shown, inline=False)
    embed.add_field(name="受信メール", value="新着メールはこのパネルの下に表示されます。", inline=False)
    embed.add_field(name="登録Gmail", value=f"`{registered_gmail}`" if gmail.get("gmail") else registered_gmail, inline=False)
    embed.add_field(name="現在のGmailエイリアス", value=f"`{alias}`" if gmail.get("current_alias") else alias, inline=False)
    embed.set_footer(text="メールの削除は表示だけを消します。Gmailは登録解除まで保存されます。")
    return embed


async def refresh_panel(interaction: discord.Interaction) -> None:
    if interaction.message:
        await interaction.message.edit(embed=personal_embed(interaction.user.id), view=MailPanelView())


class MailPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MailIssueButton())
        self.add_item(MailDeleteButton())
        self.add_item(MailCopyButton())
        self.add_item(GmailRegisterButton())
        self.add_item(GmailIssueButton())
        self.add_item(GmailDeleteButton())
        self.add_item(GmailUnregisterButton())


class MailIssueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="メールアドレスを発行", style=discord.ButtonStyle.primary, custom_id="mail_issue_button", row=0)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            address = issue_address(interaction.user.id)
            await refresh_panel(interaction)
            await interaction.followup.send(f"メールアドレスを発行しました: `{address}`", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"発行に失敗しました: {exc}", ephemeral=True)


class MailDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="メール削除", style=discord.ButtonStyle.danger, custom_id="mail_delete_button", row=0)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            address = get_current_address(interaction.user.id)
            hide_current_address(interaction.user.id)
            await delete_received_messages(interaction.client, address)
            await refresh_panel(interaction)
            await interaction.followup.send("メールの表示を削除しました。アドレス自体は停止していません。", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"削除に失敗しました: {exc}", ephemeral=True)


class MailCopyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="メールコピー", style=discord.ButtonStyle.secondary, custom_id="mail_copy_button", row=0)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        address = get_current_address(interaction.user.id)
        text = f"コピーするメールアドレス:\n`{address}`" if address else "先に「メールアドレスを発行」を押してください。"
        await interaction.followup.send(text, ephemeral=True)


class GmailRegisterModal(discord.ui.Modal, title="Gmailを登録"):
    gmail = discord.ui.TextInput(
        label="Gmailアドレス",
        placeholder="example@gmail.com",
        required=True,
        max_length=254,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            address = register_gmail(interaction.user.id, str(self.gmail.value))
            await refresh_panel(interaction)
            await interaction.followup.send(f"Gmailを登録しました: `{address}`", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Gmail登録に失敗しました: {exc}", ephemeral=True)


class GmailRegisterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Gmail登録", style=discord.ButtonStyle.success, custom_id="gmail_register_button", row=1)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(GmailRegisterModal())


class GmailIssueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="エイリアス発行", style=discord.ButtonStyle.primary, custom_id="gmail_issue_button", row=1)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            alias = issue_gmail_alias(interaction.user.id)
            await refresh_panel(interaction)
            await interaction.followup.send(f"Gmailエイリアスを発行しました: `{alias}`", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"エイリアス発行に失敗しました: {exc}", ephemeral=True)


class GmailDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="エイリアス削除", style=discord.ButtonStyle.danger, custom_id="gmail_delete_button", row=1)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            if clear_gmail_alias(interaction.user.id):
                await refresh_panel(interaction)
                await interaction.followup.send("表示中のエイリアスを削除しました。登録Gmailは残っています。", ephemeral=True)
            else:
                await interaction.followup.send("登録Gmailがありません。", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"エイリアス削除に失敗しました: {exc}", ephemeral=True)


class GmailUnregisterButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Gmail登録解除", style=discord.ButtonStyle.secondary, custom_id="gmail_unregister_button", row=1)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            if unregister_gmail(interaction.user.id):
                await refresh_panel(interaction)
                await interaction.followup.send("Gmailの登録を解除しました。発行履歴はSheetsに残ります。", ephemeral=True)
            else:
                await interaction.followup.send("登録されているGmailがありません。", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"Gmail登録解除に失敗しました: {exc}", ephemeral=True)


class MailCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="メールパネル設置", description="メールとGmailエイリアスのパネルを設置します")
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

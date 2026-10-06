from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from mail_service import (
    clear_gmail_alias,
    delete_received_messages,
    get_current_address,
    get_current_address_secret,
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
        self.add_item(IntegrationInfoButton())
        self.add_item(AddMailToVendingButton())


class MailStockCredentialView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.add_item(MailStockCredentialSelect(user_id))


class MailStockCredentialSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        options = []
        address = get_current_address(user_id)
        if address and get_current_address_secret(user_id):
            options.append(discord.SelectOption(label="通常メールアドレス", description=address[:100], value="address"))
        gmail = get_gmail_info(user_id)
        if gmail.get("current_alias") and gmail.get("current_secret"):
            options.append(discord.SelectOption(label="Gmailエイリアス", description=str(gmail["current_alias"])[:100], value="alias"))
        if not options:
            options.append(discord.SelectOption(label="発行済みの連携情報がありません", value="none"))
        super().__init__(placeholder="追加するメール情報を選択", options=options, custom_id="mail_stock_credential_select")

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
        if self.values[0] == "none":
            return await interaction.response.send_message("先にメールアドレスまたはGmailエイリアスを発行してください。", ephemeral=True)
        await interaction.response.send_message("追加先の有料自販機を選択してください。", view=MailStockMachineView(self.user_id, self.values[0]), ephemeral=True)


class MailStockMachineView(discord.ui.View):
    def __init__(self, user_id: int, credential_type: str):
        super().__init__(timeout=180)
        from Cogs.vending import load_json
        data = load_json("vending_data.json")
        options = [discord.SelectOption(label=str(vm.get("name", vm_id))[:100], value=str(vm_id)) for vm_id, vm in data.items() if isinstance(vm, dict)]
        if not options:
            options = [discord.SelectOption(label="有料自販機がありません", value="none")]
        self.add_item(MailStockMachineSelect(user_id, credential_type, options))


class MailStockMachineSelect(discord.ui.Select):
    def __init__(self, user_id: int, credential_type: str, options: list[discord.SelectOption]):
        self.user_id = user_id
        self.credential_type = credential_type
        super().__init__(placeholder="自販機を選択", options=options, custom_id="mail_stock_machine_select")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("有料自販機がありません。", ephemeral=True)
        await interaction.response.send_message(
            "追加先の商品を選択してください。",
            view=MailStockProductView(self.user_id, self.credential_type, self.values[0]),
            ephemeral=True,
        )


class MailStockProductView(discord.ui.View):
    def __init__(self, user_id: int, credential_type: str, vending_machine_id: str):
        super().__init__(timeout=180)
        from Cogs.vending import load_json
        data = load_json("vending_data.json")
        products = data.get(vending_machine_id, {}).get("products", [])
        options = [discord.SelectOption(label=str(p.get("name", "商品"))[:100], value=str(p.get("product_id"))) for p in products if not p.get("infinite_stock") and p.get("stock_file")]
        if not options:
            options = [discord.SelectOption(label="追加可能な商品がありません", value="none")]
        self.add_item(MailStockProductSelect(user_id, credential_type, vending_machine_id, options))


class MailStockProductSelect(discord.ui.Select):
    def __init__(self, user_id: int, credential_type: str, vending_machine_id: str, options: list[discord.SelectOption]):
        self.user_id = user_id
        self.credential_type = credential_type
        self.vending_machine_id = vending_machine_id
        super().__init__(placeholder="商品を選択", options=options, custom_id="mail_stock_product_select")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "none":
            return await interaction.response.send_message("追加可能な商品がありません。", ephemeral=True)
        from Cogs.vending import append_stock_content, load_json
        data = load_json("vending_data.json")
        product = next((p for p in data.get(self.vending_machine_id, {}).get("products", []) if str(p.get("product_id")) == self.values[0]), None)
        if not product or not product.get("stock_file") or product.get("infinite_stock"):
            return await interaction.response.send_message("この商品には在庫を追加できません。", ephemeral=True)
        if self.credential_type == "address":
            address = get_current_address(self.user_id)
            secret = get_current_address_secret(self.user_id)
            label = "メールアドレス"
        else:
            gmail = get_gmail_info(self.user_id)
            address = gmail.get("current_alias")
            secret = gmail.get("current_secret")
            label = "Gmailエイリアス"
        if not address or not secret:
            return await interaction.response.send_message("選択した連携情報が見つかりません。", ephemeral=True)
        append_stock_content(product["stock_file"], [f"{label}: {address} | パスワード: {secret}"])
        await interaction.response.send_message(
            f"✅ `{product.get('name', '商品')}`の在庫に1件追加しました。\n追加内容: `{address}`",
            ephemeral=True,
        )


class AddMailToVendingButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="自販機在庫に追加", style=discord.ButtonStyle.success, custom_id="mail_stock_add_button", row=2)

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("管理者権限が必要です。", ephemeral=True)
        await interaction.response.send_message(
            "自販機へ追加するメール情報を選択してください。",
            view=MailStockCredentialView(interaction.user.id),
            ephemeral=True,
        )


class MailIssueButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="メールアドレスを発行", style=discord.ButtonStyle.primary, custom_id="mail_issue_button", row=0)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            address = issue_address(interaction.user.id)
            await refresh_panel(interaction)
            secret = get_current_address_secret(interaction.user.id)
            await interaction.followup.send(
                f"メールアドレスを発行しました: `{address}`\n"
                f"連携用文字列: `{secret}`\n"
                "この文字列は連携時のパスワード等に使用できます。",
                ephemeral=True,
            )
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
            gmail_info = get_gmail_info(interaction.user.id)
            await delete_received_messages(interaction.client, gmail_info.get("current_alias"))
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


class IntegrationInfoButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="連携情報を表示", style=discord.ButtonStyle.secondary, custom_id="mail_integration_info_button", row=2)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        address = get_current_address(interaction.user.id)
        address_secret = get_current_address_secret(interaction.user.id)
        gmail = get_gmail_info(interaction.user.id)
        lines = []
        if address and address_secret:
            lines.append(f"メールアドレス: `{address}`\n連携用文字列: `{address_secret}`")
        if gmail.get("current_alias") and gmail.get("current_secret"):
            lines.append(f"Gmailエイリアス: `{gmail['current_alias']}`\n連携用文字列: `{gmail['current_secret']}`")
        if not lines:
            return await interaction.followup.send("発行済みのメールアドレスまたはGmailエイリアスがありません。", ephemeral=True)
        await interaction.followup.send(
            "\n\n".join(lines) + "\n\nこの情報は本人にだけ表示されています。",
            ephemeral=True,
        )


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
            gmail_info = get_gmail_info(interaction.user.id)
            await interaction.followup.send(
                f"Gmailエイリアスを発行しました: `{alias}`\n"
                f"連携用文字列: `{gmail_info.get('current_secret') or '発行情報を再取得してください'}`\n"
                "この文字列は連携時のパスワード等に使用できます。",
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(f"エイリアス発行に失敗しました: {exc}", ephemeral=True)


class GmailDeleteButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="エイリアス削除", style=discord.ButtonStyle.danger, custom_id="gmail_delete_button", row=1)

    async def callback(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            gmail_info = get_gmail_info(interaction.user.id)
            alias = gmail_info.get("current_alias")
            if clear_gmail_alias(interaction.user.id):
                await delete_received_messages(interaction.client, alias)
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
            gmail_info = get_gmail_info(interaction.user.id)
            alias = gmail_info.get("current_alias")
            if unregister_gmail(interaction.user.id):
                await delete_received_messages(interaction.client, alias)
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

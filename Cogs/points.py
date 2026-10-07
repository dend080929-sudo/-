from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from points_service import (
    admin_adjust,
    ensure_invite_code,
    get_balance,
    get_history,
    get_user_info,
    leaderboard,
    register_referral,
    decrypt_payout_content,
)


def _history_text(user_id: int) -> str:
    rows = get_history(user_id)
    if not rows:
        return "ポイント履歴はありません。"
    lines = []
    for row in rows:
        amount = int(row.get("amount", 0))
        sign = "+" if amount >= 0 else ""
        lines.append(f"{sign}{amount}ポイント：{row.get('reason', '不明')}（{row.get('at', '')[:19]}）")
    return "**ポイント履歴**\n" + "\n".join(lines)


def _ranking_text() -> str:
    rows = leaderboard()
    if not rows:
        return "ランキング対象者がいません。"
    lines = [f"{i}位：<@{uid}> — **{balance}ポイント**" for i, (uid, balance) in enumerate(rows, 1)]
    return "**ポイントランキング**\n" + "\n".join(lines)


class InviteRegisterModal(discord.ui.Modal, title="友達招待コードを登録"):
    code = discord.ui.TextInput(
        label="招待コード",
        placeholder="例：AVEL-AB12CD34",
        min_length=5,
        max_length=20,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        member = interaction.user
        account_created = member.created_at.timestamp() if getattr(member, "created_at", None) else None
        joined = member.joined_at.timestamp() if getattr(member, "joined_at", None) else None
        ok, message, balance = register_referral(
            member.id,
            str(self.code.value),
            account_created_at=account_created,
            guild_joined_at=joined,
        )
        prefix = "✅" if ok else "❌"
        await interaction.followup.send(f"{prefix} {message}\n現在の残高: **{balance}ポイント**", ephemeral=True)


class PointsPanelView(discord.ui.View):
    """購入者がコマンドを入力せずに使える永続ボタンパネル。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="ポイント残高", emoji="💰", style=discord.ButtonStyle.primary, custom_id="points_panel_balance")
    async def balance(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(
            f"現在のポイント残高は **{get_balance(interaction.user.id)}ポイント** です。", ephemeral=True
        )

    @discord.ui.button(label="自分の招待コード", emoji="🔗", style=discord.ButtonStyle.success, custom_id="points_panel_code")
    async def code(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_deferred(interaction, ephemeral=True)
        code = ensure_invite_code(interaction.user.id)
        await interaction.followup.send(
            f"あなたの招待コードは **`{code}`** です。\n"
            "このコードは複数の友達に使ってもらえます。招待される側は1アカウントにつき1回だけ登録できます。",
            ephemeral=True,
        )

    @discord.ui.button(label="招待コードを登録", emoji="🎁", style=discord.ButtonStyle.success, custom_id="points_panel_register")
    async def register(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(InviteRegisterModal())

    @discord.ui.button(label="履歴", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="points_panel_history")
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(_history_text(interaction.user.id), ephemeral=True)

    @discord.ui.button(label="ランキング", emoji="🏆", style=discord.ButtonStyle.secondary, custom_id="points_panel_ranking")
    async def ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(_ranking_text(), ephemeral=True)


class PointsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ポイントパネル設置", description="ポイントと友達招待の説明パネルを設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def install_panel(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        embed = discord.Embed(
            title="🎁 ポイント・友達招待",
            description=(
                "購入や友達招待でポイントが貯まります。下のボタンから確認・登録できます。\n\n"
                "**ポイント**\n"
                "・有料商品の購入：100円につき1ポイント\n"
                "・購入確認画面の「ポイントを使う」から1ポイント＝1円で値引き\n"
                "・商品価格を超えるポイントは使用できません\n"
                "・ポイント残高と履歴はBot再起動後も保存されます\n\n"
                "**友達招待**\n"
                "・招待する人：成立1人につき100ポイント\n"
                "・招待された人：初回登録で50ポイント\n"
                "・招待コードは複数の友達が利用可能\n"
                "・招待される側は1アカウントにつき1回のみ\n"
                "・自分自身や複数アカウントによる不正利用は禁止\n"
                "・作成直後のアカウントは登録できない場合があります"
            ),
            color=0xF1C40F,
        )
        embed.set_footer(text="ボタンを押すと本人にだけ結果が表示されます")
        await interaction.channel.send(embed=embed, view=PointsPanelView())
        await interaction.followup.send("ポイント・友達招待パネルを設置しました。", ephemeral=True)

    @app_commands.command(name="ポイント", description="自分のポイント残高を確認します")
    async def points(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(f"現在のポイント残高は **{get_balance(interaction.user.id)}ポイント** です。", ephemeral=True)

    @app_commands.command(name="招待コード", description="自分専用の友達招待コードを発行・確認します")
    async def invite_code(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        code = ensure_invite_code(interaction.user.id)
        await interaction.followup.send(
            f"あなたの招待コードは **`{code}`** です。\n"
            "このコードは複数の友達に使ってもらえます。\n"
            "招待される側の1アカウントにつき登録は1回だけです。",
            ephemeral=True,
        )

    @app_commands.command(name="招待登録", description="友達から受け取った招待コードを登録します（一人一回のみ）")
    @app_commands.describe(code="友達から受け取った招待コード")
    async def register_invite(self, interaction: discord.Interaction, code: str):
        await ensure_deferred(interaction, ephemeral=True)
        member = interaction.user
        ok, message, balance = register_referral(
            member.id,
            code,
            account_created_at=member.created_at.timestamp() if getattr(member, "created_at", None) else None,
            guild_joined_at=member.joined_at.timestamp() if getattr(member, "joined_at", None) else None,
        )
        prefix = "✅" if ok else "❌"
        await interaction.followup.send(f"{prefix} {message}\n現在の残高: **{balance}ポイント**", ephemeral=True)

    @app_commands.command(name="ポイント履歴", description="ポイントの最新履歴を表示します")
    async def point_history(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(_history_text(interaction.user.id), ephemeral=True)

    @app_commands.command(name="ポイントランキング", description="ポイントランキングを表示します")
    async def point_ranking(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        await interaction.followup.send(_ranking_text(), ephemeral=True)

    @app_commands.command(name="ユーザー情報", description="管理者が指定ユーザーの購入・ポイント情報を確認します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="確認するDiscordユーザー")
    async def user_info(self, interaction: discord.Interaction, member: discord.Member):
        await ensure_deferred(interaction, ephemeral=True)
        info = get_user_info(member.id)
        embed = discord.Embed(
            title="ユーザー情報",
            description=f"{member.mention}\n表示名: `{member.display_name}`\nユーザーネーム: `{member.name}`\nDiscord ID: `{member.id}`",
            color=discord.Color.blue(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="保有ポイント", value=f"**{info.get('points', 0)}ポイント**", inline=True)
        embed.add_field(name="購入回数", value=f"**{info.get('purchase_count', 0)}回**", inline=True)
        embed.add_field(name="累計支払額", value=f"**{info.get('total_paid_yen', 0):,}円**", inline=True)
        embed.add_field(name="累計使用ポイント", value=f"**{info.get('total_points_used', 0):,}ポイント**", inline=True)
        embed.add_field(name="招待コード", value=f"`{info.get('invite_code') or '未発行'}`", inline=True)
        embed.add_field(name="招待元", value=f"`{info.get('referred_by') or 'なし'}`", inline=True)
        purchases = info.get("purchases", [])[-10:]
        if purchases:
            lines = []
            for row in reversed(purchases):
                lines.append(
                    f"`{row.get('at', '')[:19]}` {row.get('product_name', '不明')} ×{row.get('quantity', 1)} "
                    f"— {int(row.get('paid_yen', 0)):,}円（{row.get('payment_method', '不明')}）"
                )
            embed.add_field(name="最近の購入（最大10件）", value="\n".join(lines)[:1024], inline=False)
        else:
            embed.add_field(name="購入履歴", value="購入履歴はありません。", inline=False)
        if await interaction.client.is_owner(interaction.user):
            payout_rows = []
            for row in reversed(purchases):
                ciphertext = row.get("payout_ciphertext", "")
                content = decrypt_payout_content(ciphertext)
                if content:
                    payout_rows.append(
                        f"【{row.get('at', '')[:19]} / {row.get('product_name', '不明')}】\n{content}"
                    )
            if payout_rows:
                payout_text = "\n\n".join(payout_rows)
                if len(payout_text) > 3900:
                    payout_text = payout_text[:3890] + "\n…（長すぎるため省略）"
                embed.add_field(
                    name="払い出し内容（Bot所有者限定）",
                    value=f"```\n{payout_text}\n```",
                    inline=False,
                )
            else:
                embed.add_field(
                    name="払い出し内容（Bot所有者限定）",
                    value="更新後の払い出し記録はありません。過去分は保存されていません。",
                    inline=False,
                )
        embed.set_footer(text="元のユーザー情報は維持。払い出し本文はBot所有者の非公開応答だけに表示します。")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="ポイント付与", description="管理者がポイントを付与します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象ユーザー", amount="付与ポイント", reason="付与理由")
    async def add_points(self, interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "管理者付与"):
        await ensure_deferred(interaction, ephemeral=True)
        if amount <= 0 or amount > 100000:
            return await interaction.followup.send("ポイントは1〜100000の範囲で指定してください。", ephemeral=True)
        balance = admin_adjust(member.id, amount, reason)
        await interaction.followup.send(f"{member.mention}に{amount}ポイント付与しました。残高: **{balance}ポイント**", ephemeral=True)

    @app_commands.command(name="ポイント減算", description="管理者がポイントを減算します")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="対象ユーザー", amount="減算ポイント", reason="減算理由")
    async def subtract_points(self, interaction: discord.Interaction, member: discord.Member, amount: int, reason: str = "管理者減算"):
        await ensure_deferred(interaction, ephemeral=True)
        if amount <= 0 or amount > 100000:
            return await interaction.followup.send("ポイントは1〜100000の範囲で指定してください。", ephemeral=True)
        balance = admin_adjust(member.id, -amount, reason)
        await interaction.followup.send(f"{member.mention}から{amount}ポイント減算しました。残高: **{balance}ポイント**", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PointsCog(bot))
    bot.add_view(PointsPanelView())

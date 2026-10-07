from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from interaction_guard import ensure_deferred
from points_service import (
    admin_adjust,
    register_invite_link,
    register_referral_by_inviter,
    get_balance,
    get_history,
    get_user_info,
    leaderboard,
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

    @discord.ui.button(label="招待リンクを発行", emoji="🔗", style=discord.ButtonStyle.success, custom_id="points_panel_link")
    async def link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await ensure_deferred(interaction, ephemeral=True)
        cog = interaction.client.get_cog("PointsCog")
        try:
            url = await cog.create_invite_link(interaction)
            await interaction.followup.send(
                f"あなたの招待リンクです：{url}\nこのリンクからサーバーに参加すると、招待成立時に自動でポイントが付与されます。",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send("招待リンクを作成できません。Botに「招待を作成」権限を付与してください。", ephemeral=True)
        except (discord.HTTPException, AttributeError) as exc:
            print(f"招待リンク作成エラー: {exc}")
            await interaction.followup.send("招待リンクを作成できませんでした。招待を作成できるチャンネルで再試行してください。", ephemeral=True)
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
        self._invite_uses: dict[int, dict[str, int]] = {}

    async def create_invite_link(self, interaction: discord.Interaction) -> str:
        if not interaction.guild or not interaction.channel:
            raise AttributeError("guild/channel required")
        invite = await interaction.channel.create_invite(max_age=0, max_uses=0, unique=True, reason="ポイント招待リンク")
        register_invite_link(interaction.user.id, invite.code, str(invite), interaction.guild.id, interaction.channel.id)
        self._invite_uses.setdefault(interaction.guild.id, {})[invite.code] = invite.uses or 0
        return str(invite)

    async def _refresh_invites(self, guild: discord.Guild) -> list[discord.Invite]:
        invites = await guild.invites()
        self._invite_uses[guild.id] = {invite.code: (invite.uses or 0) for invite in invites}
        return invites

    @commands.Cog.listener()
    async def on_ready(self):
        for guild in self.bot.guilds:
            try:
                await self._refresh_invites(guild)
            except discord.HTTPException as exc:
                print(f"招待一覧の取得に失敗しました({guild.id}): {exc}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        try:
            invites = await member.guild.invites()
        except discord.HTTPException as exc:
            print(f"招待利用状況の取得に失敗しました({member.guild.id}): {exc}")
            return
        previous = self._invite_uses.get(member.guild.id, {})
        used = [invite for invite in invites if (invite.uses or 0) > previous.get(invite.code, 0)]
        self._invite_uses[member.guild.id] = {invite.code: (invite.uses or 0) for invite in invites}
        if not used:
            return
        # 同時参加時は利用数の増加が最も大きい招待を優先する。
        invite = max(used, key=lambda item: (item.uses or 0) - previous.get(item.code, 0))
        from points_service import get_invite_link
        tracked = get_invite_link(invite.code)
        if not tracked:
            return
        ok, message, balance = register_referral_by_inviter(
            member.id, tracked["inviter_id"],
            account_created_at=member.created_at.timestamp() if getattr(member, "created_at", None) else None,
        )
        if ok:
            print(f"Discord招待成立: inviter={tracked['inviter_id']} invitee={member.id} ({message})")

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
                "・招待リンクからのサーバー参加を自動検出\n"
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

    @app_commands.command(name="招待リンク", description="自分専用の友達招待リンクを発行します")
    async def invite_link(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        try:
            url = await self.create_invite_link(interaction)
            await interaction.followup.send(f"あなたの招待リンクです：{url}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("Botに「招待を作成」権限がありません。", ephemeral=True)
        except (discord.HTTPException, AttributeError) as exc:
            print(f"招待リンク作成エラー: {exc}")
            await interaction.followup.send("招待リンクを作成できませんでした。", ephemeral=True)
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
        embed.add_field(name="招待リンク", value="発行済みリンクは参加時に自動判定", inline=True)
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

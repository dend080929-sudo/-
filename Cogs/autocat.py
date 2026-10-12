from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import os
import secrets
import sys
import time

import discord
from discord import app_commands
from discord.ext import commands

try:
    import autocat_main as autocat
except Exception as exc:  # Keep the main bot usable if AutoCat dependencies fail.
    autocat = None
    print(f"[AUTOCAT DISCORD] backend import failed: {type(exc).__name__}: {exc}")


# The Discord vending UI intentionally exposes the basic, fixed-value actions only.
# Advanced per-character and detailed VIP editors are not included in this Discord
# panel; billing is always calculated by the same server-side AutoCat API.
CATALOG = {
    "items": (
        "アイテム",
        [
            ("猫缶 58000", "catfood"), ("XP 99999999", "xp"), ("NP 9999", "np"),
            ("にゃんこチケット 999", "normal_tickets"), ("レアチケット 999", "rare_tickets"),
            ("プラチナチケット 20", "platinum_tickets"), ("レジェンドチケット 10", "legend_tickets"),
            ("リーダーシップ 999", "leadership"), ("バトルアイテム全種", "battle_items"),
            ("キャッツアイ全種", "catseyes"), ("ネコビタン全種", "catamins"),
            ("城素材全種", "base_materials"), ("マタタビ全種", "matatabi"),
            ("本能玉全種", "talent_orbs"),
        ],
    ),
    "stages": (
        "ステージ",
        [
            ("メインステージ(お宝金＆クリア)", "main_story_clear"),
            ("メインゾンビステージ全クリア", "zombie_clear"), ("魔界編全クリア", "aku_clear"),
            ("レジェンド全クリア", "legend_clear"), ("真レジェンド全クリア", "true_legend_clear"),
            ("零レジェンド全クリア", "zero_legend_clear"), ("イベントステージ全クリア", "event_clear"),
            ("全ステージ全クリア", "all_stage_clear"),
        ],
    ),
    "system": (
        "システム",
        [
            ("オトート全城強化Lv.Max", "ototo_max"), ("にゃんこ神社Lv.Max", "cat_shrine_max"),
            ("全施設 Lv.Max", "base_upgrades_max"), ("ゴールド会員化", "gold_membership"),
            ("編成スロット数最大化", "slots_max"), ("にゃんこメダル全解放", "medals_all"),
            ("チュートリアルスキップ", "skip_tutorial"), ("ランクアップポップアップ非表示", "hide_rank_up"),
            ("敵キャラ図鑑全解放", "unlock_enemy_guide"), ("プレイ時間 +720時間", "add_playtime_720h"),
        ],
    ),
    "gamatoto": (
        "ガマトト",
        [("ガマトトLv.Max", "gamatoto_max"), ("ガマトト助手全レジェンド化", "gamatoto_helpers")],
    ),
    "characters": (
        "キャラクター",
        [
            ("全キャラ解放", "unlock_all_cats"), ("全キャラLv.Max+", "max_all_cats_plus"),
            ("全キャラLv.Max", "max_all_cats"), ("全キャラ最大形態化", "true_form_all"),
            ("全キャラ本能解放", "talents_all"), ("エラーキャラ削除", "remove_error_cats"),
        ],
    ),
}
CATALOG_API_GROUP = {
    "items": "s1", "stages": "s1", "system": "s2", "gamatoto": "s2", "characters": "s3",
}


def serialize_selected_categories(selected_by_category: dict[str, set[str]]) -> dict[str, list[str]]:
    """Map display categories to the existing AutoCat API's s1/s2/s3 groups."""
    payload = {"s1": [], "s2": [], "s3": []}
    for category, values in selected_by_category.items():
        api_group = CATALOG_API_GROUP[category]
        payload[api_group].extend(sorted(values))
    return payload
OPERATION_LABELS = {"daiko": "引き継ぎコード代行", "create": "新規アカウント作成", "clone": "アカウント複製"}


def _backend_ready() -> bool:
    return bool(autocat is not None and getattr(autocat, "app", None) is not None)


class AutoCatClient:
    """An isolated Flask test client carrying a signed Discord identity session."""
    def __init__(self, discord_user: discord.abc.User):
        self.client = autocat.app.test_client()
        user_digest = hashlib.sha256(str(discord_user.id).encode("ascii")).hexdigest()
        self.rate_limit_ip = f"198.18.{int(user_digest[:2], 16)}.{int(user_digest[2:4], 16)}"
        with self.client.session_transaction() as session:
            session["discord_user"] = {
                "id": str(discord_user.id),
                "username": str(discord_user.name),
                "global_name": str(getattr(discord_user, "global_name", "") or discord_user.name),
                "avatar": None,
            }

    def submit(self, endpoint: str, data: dict) -> tuple[int, dict]:
        payload = dict(data)
        if endpoint in {"/api/run_daiko", "/api/run_create", "/api/run_clone"}:
            payload["api_key"] = autocat.generate_api_key()
        response = self.client.post(
            endpoint,
            json=payload,
            headers={
                "CF-Connecting-IP": self.rate_limit_ip,
                "X-AutoCat-Internal": autocat.AUTOCAT_INTERNAL_TOKEN,
            },
        )
        return response.status_code, response.get_json(silent=True) or {"error": "サーバーから不正な応答がありました。"}

    def status(self, job_id: str, job_token: str) -> dict:
        response = self.client.get(
            f"/api/job/{job_id}",
            headers={
                "X-Job-Token": job_token,
                "X-AutoCat-Internal": autocat.AUTOCAT_INTERNAL_TOKEN,
            },
        )
        return response.get_json(silent=True) or {"status": "unknown", "error": "処理状況を取得できません。"}


class OperationSelect(discord.ui.Select):
    def __init__(self, wizard: "AutoCatWizard"):
        self.wizard = wizard
        super().__init__(
            placeholder="利用するサービスを選択",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label=v, value=k) for k, v in OPERATION_LABELS.items()],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        self.wizard.operation = self.values[0]
        await self.wizard.refresh(interaction)


class CategorySelect(discord.ui.Select):
    def __init__(self, wizard: "AutoCatWizard"):
        self.wizard = wizard
        super().__init__(
            placeholder="代行するカテゴリ（複数選択可）",
            min_values=1,
            max_values=len(CATALOG),
            options=[
                discord.SelectOption(label=v[0], value=k, default=k in wizard.categories)
                for k, v in CATALOG.items()
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen = set(self.values)
        removed = self.wizard.categories - chosen
        for key in removed:
            self.wizard.selected[key].clear()
        self.wizard.categories = chosen
        for child in tuple(self.wizard.children):
            if isinstance(child, (CategorySelect, OperationSelect)) or (
                isinstance(child, discord.ui.Button) and child.label == "入力・注文へ進む"
            ):
                self.wizard.remove_item(child)
        for row, key in enumerate(category for category in CATALOG if category in chosen):
            self.wizard.add_item(ItemSelect(self.wizard, key, row=row))
        await interaction.response.edit_message(content=self.wizard.summary(), view=self.wizard)
        await interaction.followup.send(
            "すべてのカテゴリで代行項目を選び終えたら、こちらから進んでください。",
            view=AutoCatContinueView(self.wizard),
            ephemeral=True,
        )


class ItemSelect(discord.ui.Select):
    def __init__(self, wizard: "AutoCatWizard", category_key: str, *, row: int):
        self.wizard = wizard
        self.category_key = category_key
        title, items = CATALOG[category_key]
        picked = wizard.selected.get(category_key, set())
        options = [
            discord.SelectOption(label=label[:100], value=value, default=value in picked)
            for label, value in items
        ]
        super().__init__(
            placeholder=f"{title}から項目を選択（複数可）",
            min_values=0,
            max_values=len(options),
            options=options,
            row=row,
        )

    async def callback(self, interaction: discord.Interaction):
        self.wizard.selected[self.category_key] = set(self.values)
        self.wizard.remove_item(self)
        row = next(index for index, key in enumerate(category for category in CATALOG if category in self.wizard.categories) if key == self.category_key)
        self.wizard.add_item(ItemSelect(self.wizard, self.category_key, row=row))
        await interaction.response.edit_message(
            content=self.wizard.summary(),
            view=self.wizard,
        )


class AutoCatWizard(discord.ui.View):
    def __init__(self, cog: "AutoCatCog", owner_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.owner_id = owner_id
        self.operation = "daiko"
        self.categories: set[str] = set()
        self.selected: dict[str, set[str]] = {key: set() for key in CATALOG}
        self.add_item(OperationSelect(self))
        self.add_item(CategorySelect(self))

    def summary(self) -> str:
        item_count = sum(len(v) for v in self.selected.values())
        operation_label = OPERATION_LABELS[self.operation]
        if self.operation == "clone":
            status = "複製する個数: **1個**（注文モーダルで1〜2個を指定）\n料金は複製個数で計算します。項目カタログの選択は複製には適用されません。"
        elif self.operation == "create":
            item_count = max(1, item_count)
            status = f"選択項目: **{item_count}個**\n新規作成数は注文モーダルで1〜2個から指定できます。"
        else:
            status = f"選択項目: **{item_count}個**"
        chosen_categories = "、".join(CATALOG[key][0] for key in CATALOG if key in self.categories) or "未選択"
        status += f"\n選択カテゴリ: {chosen_categories}"
        if self.categories and self.operation != "clone":
            status += "\n下の各カテゴリのドロップダウンから代行内容を選択してください。"
        unit_price = int(getattr(autocat, "AUTOCAT_ITEM_PRICE_YEN", 50)) if autocat else 50
        status += f"\n\n初回は成功3回まで無料。その後は1項目{unit_price}円です。VIPロール保持者は無料です。"
        return f"## にゃんこ代行自販機\nサービス: **{operation_label}**\n{status}"

    async def refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content=self.summary(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("この注文パネルを開いた本人だけ操作できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="入力・注文へ進む", style=discord.ButtonStyle.success, row=4)
    async def continue_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.open_input(interaction)

    async def open_input(self, interaction: discord.Interaction):
        if self.operation != "clone" and not any(self.selected.values()):
            await interaction.response.send_message("カテゴリを選び、表示された各項目メニューから代行内容を選択してください。", ephemeral=True)
            return
        await interaction.response.send_modal(AutoCatInputModal(self))


class AutoCatContinueView(discord.ui.View):
    def __init__(self, wizard: AutoCatWizard):
        super().__init__(timeout=900)
        self.wizard = wizard

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.wizard.owner_id:
            await interaction.response.send_message("この注文を開始した本人だけ操作できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="入力・注文へ進む", style=discord.ButtonStyle.success)
    async def continue_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.wizard.open_input(interaction)


class AutoCatPanelView(discord.ui.View):
    """Shared vending panel; each click opens a private, user-bound order."""
    def __init__(self, cog: "AutoCatCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="にゃんこ代行を注文", style=discord.ButtonStyle.success, custom_id="autocat_vending:open")
    async def open_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_panel(interaction, public=False)


class AutoCatInputModal(discord.ui.Modal):
    def __init__(self, wizard: AutoCatWizard):
        super().__init__(title=f"{OPERATION_LABELS[wizard.operation]} — 注文情報")
        self.wizard = wizard
        if wizard.operation in {"daiko", "clone"}:
            self.transfer_code = discord.ui.TextInput(label="引き継ぎコード", max_length=32, required=True)
            self.auth_code = discord.ui.TextInput(label="認証番号（4桁）", min_length=4, max_length=4, required=True)
            self.add_item(self.transfer_code)
            self.add_item(self.auth_code)
        if wizard.operation in {"create", "clone"}:
            self.count = discord.ui.TextInput(
                label="作成・複製する個数（1〜2）",
                min_length=1,
                max_length=2,
                default="1",
                required=True,
            )
            self.add_item(self.count)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = {
            "selected": serialize_selected_categories(self.wizard.selected),
            "char_list": [],
            "custom_amounts": {},
            "custom_playtime": "",
            "fingerprint": secrets.token_hex(32),
        }
        if self.wizard.operation in {"daiko", "clone"}:
            payload["transfer_code"] = self.transfer_code.value.strip()
            payload["auth_code"] = self.auth_code.value.strip()
            payload["operation_id"] = autocat._generate_operation_id()
        if self.wizard.operation in {"create", "clone"}:
            try:
                count = int(self.count.value)
            except (TypeError, ValueError):
                await interaction.followup.send("個数は1〜2の数字で入力してください。", ephemeral=True)
                return
            if not 1 <= count <= 2:
                await interaction.followup.send("個数は1〜2の範囲で入力してください。", ephemeral=True)
                return
            payload["count"] = count
        endpoint = {
            "daiko": "/api/run_daiko",
            "create": "/api/run_create",
            "clone": "/api/run_clone",
        }[self.wizard.operation]
        await self.wizard.cog.begin_order(interaction, endpoint, payload, self.wizard.operation)


class PayPayModal(discord.ui.Modal, title="PayPayで支払う"):
    def __init__(self, order: "PendingOrder"):
        super().__init__()
        self.order = order
        self.link = discord.ui.TextInput(label="PayPayリンク", placeholder="https://pay.paypay.ne.jp/...", max_length=100, required=True)
        self.add_item(self.link)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = dict(self.order.payload)
        payload.update(payment_method="paypay", paypay_link=self.link.value.strip())
        await self.order.cog.submit_paid(interaction, self.order, payload)


class PaymentChoice(discord.ui.View):
    def __init__(self, order: "PendingOrder", quote: dict):
        super().__init__(timeout=600)
        self.order = order
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "共通ポイントで支払う":
                item.disabled = not bool(quote.get("points_available", True))
            elif isinstance(item, discord.ui.Button) and item.label == "PayPayリンクで支払う":
                item.disabled = not bool(quote.get("paypay_available", True))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.order.user.id:
            await interaction.response.send_message("この支払い画面を開いた本人だけ操作できます。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="共通ポイントで支払う", style=discord.ButtonStyle.primary)
    async def points(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        payload = dict(self.order.payload)
        payload["payment_method"] = "points"
        await self.order.cog.submit_paid(interaction, self.order, payload)

    @discord.ui.button(label="PayPayリンクで支払う", style=discord.ButtonStyle.secondary)
    async def paypay(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PayPayModal(self.order))


class PendingOrder:
    def __init__(self, cog: "AutoCatCog", user: discord.abc.User, endpoint: str, payload: dict, operation: str, client: AutoCatClient):
        self.cog, self.user, self.endpoint = cog, user, endpoint
        self.payload, self.operation, self.client = copy.deepcopy(payload), operation, client
        self.created_at = time.time()


class AutoCatCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._auto_install_lock = asyncio.Lock()
        self._achievement_lock = asyncio.Lock()
        self._auto_install_done = False

    @commands.Cog.listener()
    async def on_ready(self):
        """Install/reuse the public panel in the configured channel on startup."""
        if self._auto_install_done:
            return
        raw_channel_id = (os.getenv("AUTOCAT_PANEL_CHANNEL_ID") or "").strip()
        if not raw_channel_id:
            return
        try:
            channel_id = int(raw_channel_id)
            if channel_id <= 0:
                raise ValueError("channel id must be positive")
        except ValueError:
            print("[AUTOCAT DISCORD] AUTOCAT_PANEL_CHANNEL_ID is invalid; automatic panel installation skipped")
            self._auto_install_done = True
            return

        async with self._auto_install_lock:
            if self._auto_install_done:
                return
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[AUTOCAT DISCORD] auto-install channel lookup failed: {type(exc).__name__}")
                    return
            if not hasattr(channel, "send") or not hasattr(channel, "history"):
                print("[AUTOCAT DISCORD] configured auto-install target is not a message channel")
                self._auto_install_done = True
                return

            view = AutoCatPanelView(self)
            try:
                async for message in channel.history(limit=50):
                    if message.author.id != self.bot.user.id:
                        continue
                    has_autocat_button = any(
                        getattr(component, "custom_id", None) == "autocat_vending:open"
                        for row in message.components
                        for component in getattr(row, "children", ())
                    )
                    if has_autocat_button:
                        await message.edit(content=self._panel_text(), view=view)
                        self._auto_install_done = True
                        print(f"✅ AutoCat vending panel reused in channel {channel_id}")
                        return
                await channel.send(self._panel_text(), view=view)
                self._auto_install_done = True
                print(f"✅ AutoCat vending panel automatically installed in channel {channel_id}")
            except (discord.Forbidden, discord.HTTPException) as exc:
                print(f"[AUTOCAT DISCORD] auto-install send/history failed: {type(exc).__name__}")

    @staticmethod
    def _panel_text() -> str:
        price = int(getattr(autocat, "AUTOCAT_ITEM_PRICE_YEN", 50)) if autocat else 50
        return (
            "## にゃんこ代行自販機\n"
            "ボタンからサービス・項目を選び、注文と決済をDiscord内で進められます。\n"
            f"初回成功3回まで無料、以後1項目{price}円。VIPロール保持者は無料です。"
        )

    async def show_panel(self, interaction: discord.Interaction, *, public: bool = False):
        if not _backend_ready():
            await interaction.response.send_message("AutoCatの準備ができていません。管理者へ連絡してください。", ephemeral=True)
            return
        view = AutoCatWizard(self, interaction.user.id)
        await interaction.response.send_message(
            view.summary() + "\nDiscordのメニューから操作を選んでください。",
            view=view,
            ephemeral=not public,
        )

    async def begin_order(self, interaction: discord.Interaction, endpoint: str, payload: dict, operation: str):
        client = AutoCatClient(interaction.user)
        status_code, result = await asyncio.to_thread(client.submit, endpoint, payload)
        if status_code == 402 and result.get("code") == "payment_required":
            order = PendingOrder(self, interaction.user, endpoint, payload, operation, client)
            amount = int(result.get("amount_yen", 0))
            count = int(result.get("item_count", 0))
            unit = int(result.get("unit_price_yen", getattr(autocat, "AUTOCAT_ITEM_PRICE_YEN", 50)))
            price = f"今回の料金: **{amount:,}円**（{count}項目 × {unit}円）"
            await interaction.followup.send(
                f"初回無料分を使い切っています。{price}\n共通ポイントまたはPayPayを選んでください。",
                view=PaymentChoice(order, result),
                ephemeral=True,
            )
            return
        if status_code != 200:
            await interaction.followup.send(str(result.get("error") or "注文を受け付けられませんでした。"), ephemeral=True)
            return
        await self._accepted(interaction, client, result, operation)

    async def submit_paid(self, interaction: discord.Interaction, order: PendingOrder, payload: dict):
        status_code, result = await asyncio.to_thread(order.client.submit, order.endpoint, payload)
        if status_code != 200:
            text = str(result.get("error") or "決済・注文に失敗しました。")
            if status_code == 402 and result.get("points_balance") is not None:
                text += f"\n現在のポイント残高: {int(result['points_balance']):,}ポイント"
            await interaction.followup.send(text, ephemeral=True)
            return
        await self._accepted(interaction, order.client, result, order.operation)

    async def _accepted(self, interaction: discord.Interaction, client: AutoCatClient, result: dict, operation: str):
        job_id = str(result.get("job_id") or "")
        job_token = str(result.get("job_token") or "")
        await interaction.followup.send(
            f"注文を受け付けました。\nサービス: **{OPERATION_LABELS[operation]}**\n受付ID: `{result.get('operation_id') or job_id}`\n処理が完了したら、このDiscordアカウントへ結果をDMします。",
            ephemeral=True,
        )
        asyncio.create_task(self._deliver_result(client, job_id, job_token, interaction, operation))

    async def _deliver_result(self, client: AutoCatClient, job_id: str, token: str, interaction: discord.Interaction, operation: str):
        user = interaction.user
        deadline = time.monotonic() + 60 * 60
        last_log = "受付済み"
        while time.monotonic() < deadline:
            try:
                result = await asyncio.to_thread(client.status, job_id, token)
            except Exception as exc:
                print(f"[AUTOCAT DISCORD] job poll failed: {type(exc).__name__}")
                await asyncio.sleep(10)
                continue
            status = str(result.get("status") or "unknown")
            last_log = str(result.get("log") or last_log)[:500]
            if status in {"done", "error", "unknown"}:
                await self._send_result(user, result, operation, interaction)
                return
            await asyncio.sleep(8)
        try:
            await user.send(f"AutoCat受付 `{job_id}` はまだ処理中です。時間をおいて管理者へ受付IDをお伝えください。\n最新状況: {last_log}")
        except discord.HTTPException:
            pass

    async def _post_public_achievement(self, user, result: dict, operation: str, interaction: discord.Interaction):
        """Post successful AutoCat jobs in the Tsum-style public achievement channel."""
        tsum = sys.modules.get("tsum_bot")
        guild = getattr(interaction, "guild", None)
        channel = None
        if tsum and guild:
            try:
                channel_id = tsum.guild_setting(guild.id, "public_result_channel_id")
                if channel_id:
                    channel = self.bot.get_channel(int(channel_id))
                    if channel is None:
                        channel = await self.bot.fetch_channel(int(channel_id))
            except Exception as exc:
                print(f"[AUTOCAT DISCORD] achievement channel lookup failed: {type(exc).__name__}")

        async with self._achievement_lock:
            current = None
            if tsum:
                try:
                    counter_id = tsum.CONFIG.get("achievement_channel_id")
                    counter_channel = None
                    if counter_id:
                        counter_channel = self.bot.get_channel(int(counter_id))
                        if counter_channel is None:
                            counter_channel = await self.bot.fetch_channel(int(counter_id))
                    if counter_channel is not None:
                        current = await tsum._achv_get_count(counter_channel)
                    elif channel is not None:
                        current = await tsum._achv_count_messages(channel)
                except Exception as exc:
                    print(f"[AUTOCAT DISCORD] achievement counter lookup failed: {type(exc).__name__}")
                if not isinstance(current, int):
                    current = tsum.achievement_count()
                current = max(0, int(current or 0))
                achievement_number = current
                try:
                    tsum.set_achievement_count(current)
                    tsum.increment_achievement()
                except Exception:
                    pass
                tsum.request_achievement_update()
            else:
                current = await tsum._achv_count_messages(channel) if channel else 0
                achievement_number = max(0, int(current or 0))

            if channel is None:
                return

            display_name = str(getattr(user, "display_name", None) or getattr(user, "name", "利用者"))
            avatar = getattr(getattr(user, "display_avatar", None), "url", None)
            embed = discord.Embed(color=0x2ECC71)
            if avatar:
                embed.set_author(name=display_name, icon_url=avatar)
                embed.set_thumbnail(url=avatar)
            else:
                embed.set_author(name=display_name)
            item_count = max(1, int(result.get("billing_item_count") or 1))
            method = str(result.get("billing_method") or "").lower()
            amount = max(0, int(result.get("billing_amount_yen") or 0))
            if method == "points":
                payment_text = f"**支払い**\n**{amount:,}ポイント**"
            elif method == "paypay":
                payment_text = f"**金額**\n**¥{amount:,}**\n**支払い方法**\nPayPay"
            elif method == "vip":
                payment_text = "**金額**\n**無料**\n**利用区分**\nVIP"
            elif method == "free":
                payment_text = "**金額**\n**無料**\n**利用区分**\n初回無料枠"
            else:
                payment_text = "**金額**\n**無料**"
            embed.description = (
                f"**ご利用者**\n**{getattr(user, 'mention', display_name)}**\n"
                f"**注文内容**\n**{OPERATION_LABELS[operation]}（{item_count}項目）**\n"
                f"**実績番号**\n**-{achievement_number:02d}**\n{payment_text}"
            )
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except Exception as exc:
                print(f"[AUTOCAT DISCORD] achievement post failed: {type(exc).__name__}")

    async def _send_result(self, user: discord.abc.User, result: dict, operation: str, interaction: discord.Interaction):
        status = str(result.get("status") or "unknown")
        if status != "done":
            text = f"AutoCatの処理に失敗しました。\n理由: {str(result.get('error') or '不明なエラー')[:800]}"
            if result.get("admin_recovery_required") and result.get("operation_id"):
                text += f"\n受付ID: `{result['operation_id']}`。同じ操作を再実行せず、管理者へ連絡してください。"
            try:
                await user.send(text)
            except discord.HTTPException:
                try:
                    await interaction.followup.send(text, ephemeral=True)
                except discord.HTTPException:
                    print(f"[AUTOCAT DISCORD] unable to deliver error result for {user.id}")
            return
        embed = discord.Embed(title="AutoCatの処理が完了しました", color=discord.Color.green())
        embed.add_field(name="サービス", value=OPERATION_LABELS[operation], inline=False)
        applied = result.get("applied") or []
        if applied:
            embed.add_field(name="適用内容", value="\n".join(str(v) for v in applied)[:1000], inline=False)
        files = []
        accounts = result.get("accounts") or result.get("copies") or []
        if not accounts and result.get("transfer_code"):
            accounts = [{"tc": result.get("transfer_code"), "ac": result.get("auth_code")}]
        if accounts:
            body = "\n".join(f"{index}. 引き継ぎコード: {item.get('tc','')} / 認証番号: {item.get('ac','')}" for index, item in enumerate(accounts, 1))
            file = discord.File(fp=io.BytesIO(body.encode("utf-8")), filename="autocat_codes.txt")
            files.append(file)
            embed.add_field(name="引き継ぎコード", value="安全のため添付ファイルでお送りします。", inline=False)
        try:
            await user.send(embed=embed, files=files)
        except discord.HTTPException:
            try:
                for file in files:
                    file.fp.seek(0)
                await interaction.followup.send(embed=embed, files=files, ephemeral=True)
            except discord.HTTPException:
                print(f"[AUTOCAT DISCORD] unable to deliver success result for {user.id}")
        await self._post_public_achievement(user, result, operation, interaction)

    @app_commands.command(name="にゃんこ自販機設置", description="AutoCatのDiscord注文パネルをこのチャンネルに設置します")
    @app_commands.checks.has_permissions(administrator=True)
    async def autocat_install(self, interaction: discord.Interaction):
        if not _backend_ready():
            if interaction.response.is_done():
                await interaction.followup.send("AutoCatの準備ができていません。", ephemeral=True)
            else:
                await interaction.response.send_message("AutoCatの準備ができていません。", ephemeral=True)
            return
        view = AutoCatPanelView(self)
        if interaction.response.is_done():
            await interaction.followup.send(self._panel_text(), view=view, ephemeral=False)
        else:
            await interaction.response.send_message(self._panel_text(), view=view, ephemeral=False)
        try:
            await interaction.followup.send("パネルを設置しました。", ephemeral=True)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot):
    if _backend_ready():
        cog = AutoCatCog(bot)
        await bot.add_cog(cog)
        bot.add_view(AutoCatPanelView(cog))
        print("✅ AutoCat Discord vending panel loaded")
    else:
        print("⚠️ AutoCat Discord panel skipped: backend unavailable")

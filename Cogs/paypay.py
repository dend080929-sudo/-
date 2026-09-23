import discord
from interaction_guard import ensure_deferred
from discord import ui
from discord.ext import commands
from discord import app_commands
import json
import os
import uuid
from utils import is_allowed
import paypayu
from persistent_store import load_json_store, save_json_store

PAYPAY_DATA_FILE = "paypay_data.json"
VENDING_DATA_FILE = "vending_data.json"

def load_vending_data():
    if os.path.exists(VENDING_DATA_FILE):
        try:
            with open(VENDING_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error: {VENDING_DATA_FILE} のJSON形式が不正です。")
            return {}
    return {}

def save_vending_data(data):
    with open(VENDING_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def load_paypay_data():
    return load_json_store("paypay_accounts", PAYPAY_DATA_FILE)

def save_paypay_data(data):
    save_json_store("paypay_accounts", data, PAYPAY_DATA_FILE)

class PayPayModal(ui.Modal, title="PayPay OTP認証"):
    def __init__(self, phone, password, uuid, otpid, otp_pre):
        super().__init__(timeout=300)
        self.phone = phone
        self.password = password
        self.uuid = uuid
        self.otpid = otpid
        self.otp_pre = otp_pre

    otp_input = ui.TextInput(
        label="ワンタイムパスワード",
        placeholder="SMSに届いた4桁の認証コードを入力",
        min_length=4,
        max_length=4,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await ensure_deferred(interaction, ephemeral=True)
        
        otp_result = await paypayu.login_otp(
            self.uuid,
            self.otp_input.value,
            self.otpid,
            self.otp_pre
        )

        if otp_result == "OK":
            paypay_data = load_paypay_data()
            user_id_str = str(interaction.user.id)
            
            paypay_data[user_id_str] = {
                "phone": self.phone,
                "password": self.password,
                "uuid": self.uuid
            }
            save_paypay_data(paypay_data)
            
            # vending_data.json の paypay_id を自動設定
            vending_data = load_vending_data()
            updated_count = 0
            
            for vm_id, vm_data in vending_data.items():
                if (
                    str(vm_data.get("owner_id")) == user_id_str
                    and vm_data.get("paypay_id") is None
                ):
                    vm_data["paypay_id"] = user_id_str
                    updated_count += 1
            
            if updated_count > 0:
                save_vending_data(vending_data)
            
            embed = discord.Embed(
                title="PayPay登録完了",
                description="PayPayアカウント情報の登録が完了しました。",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif otp_result == "ERR":
            embed = discord.Embed(
                title="PayPayログインエラー",
                description="OTPコードが正しくありません。",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        else:
            print(f"OTP: {otp_result}")
            embed = discord.Embed(
                title="PayPayログインエラー",
                description="開発者にお問い合わせください。",
                color=discord.Color.orange()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


class PayPayOTPView(ui.View):
    def __init__(self, phone, password, uuid, otpid, otp_pre):
        super().__init__(timeout=300)
        self.phone = phone
        self.password = password
        self.uuid = uuid
        self.otpid = otpid
        self.otp_pre = otp_pre

    @ui.button(label="OTP入力画面を開く", style=discord.ButtonStyle.primary)
    async def open_otp_modal(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(
            PayPayModal(
                self.phone,
                self.password,
                self.uuid,
                self.otpid,
                self.otp_pre,
            )
        )


class PaypayCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        if not os.path.exists(PAYPAY_DATA_FILE):
            save_paypay_data({})

    @app_commands.command(
        name="ペイペイログイン",
        description="PayPayアカウントにログインします"
    )
    @is_allowed()
    @app_commands.describe(
        phone="電話番号",
        password="パスワード"
    )
    async def paypay_register(
        self,
        interaction: discord.Interaction,
        phone: str,
        password: str
    ):
        # PayPay通信が3秒を超えても「アプリが応答しない」にならないよう、先に応答を確定する
        await ensure_deferred(interaction, ephemeral=True)
        set_uuid = str(uuid.uuid4())

        try:
            result = await paypayu.login(phone, password, set_uuid)
        except Exception as e:
            print(f"PayPay login request failed: {e}")
            await interaction.followup.send(
                "PayPayへの接続に失敗しました。時間を置いて再試行してください。",
                ephemeral=True,
            )
            return

        if not isinstance(result, dict) or result.get("response_type") == "ErrorResponse":
            embed = discord.Embed(
                title="PayPayログインエラー",
                description=(
                    "```"
                    "ログイン情報とパスワードが一致していません。\n"
                    "情報を正しく入力してください。"
                    "```"
                ),
                color=0xff3333
            )
            await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )
            return

        try:
            otpid = result["otp_reference_id"]
            otp_pre = result["otp_prefix"]
        except KeyError:
            await interaction.followup.send(
                "PayPayからOTP認証情報を取得できませんでした。入力内容やPayPay側の状態を確認してください。",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "PayPayから届いたOTPコードを入力してください。",
            view=PayPayOTPView(phone, password, set_uuid, otpid, otp_pre),
            ephemeral=True,
        )

    # =========================
    # PayPayログアウトコマンド
    # =========================
    @app_commands.command(
        name="ペイペイログアウト",
        description="PayPayアカウントをログアウトします"
    )
    @is_allowed()
    async def paypay_logout(self, interaction: discord.Interaction):

        paypay_data = load_paypay_data()
        user_id_str = str(interaction.user.id)

        # 登録確認
        if user_id_str not in paypay_data:
            embed = discord.Embed(
                title="PayPayログアウト",
                description="PayPayアカウントは登録されていません。",
                color=discord.Color.red()
            )
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
            return

        # データ削除
        del paypay_data[user_id_str]
        save_paypay_data(paypay_data)

        # vending_data.json の paypay_id 解除
        vending_data = load_vending_data()

        for vm_id, vm_data in vending_data.items():
            if vm_data.get("paypay_id") == user_id_str:
                vm_data["paypay_id"] = None

        save_vending_data(vending_data)

        embed = discord.Embed(
            title="PayPayログアウト完了",
            description="PayPayアカウント情報を削除しました。",
            color=discord.Color.green()
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    # =========================
    # PayPayプロキシ設定コマンド
    # =========================
    @app_commands.command(
        name="ペイペイプロキシ設定",
        description="PayPayリクエストに使用するプロキシURLを設定・更新します"
    )
    @is_allowed()
    @app_commands.describe(
        proxy_url="プロキシURL (例: http://user:pass@ip:port/ ) ※外す場合は「none」"
    )
    async def paypay_proxy_set(
        self,
        interaction: discord.Interaction,
        proxy_url: str
    ):
        if proxy_url.lower() == "none":
            paypayu.save_proxy("")
            embed = discord.Embed(
                title="プロキシ設定解除",
                description="プロキシの設定を解除しました（直差しで通信します）。",
                color=discord.Color.blue()
            )
        else:
            if not (proxy_url.startswith("http://") or proxy_url.startswith("https://")):
                embed = discord.Embed(
                    title="設定エラー",
                    description="プロキシURLは `http://` または `https://` から開始してください。",
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            paypayu.save_proxy(proxy_url)
            embed = discord.Embed(
                title="プロキシ設定完了",
                description=f"プロキシURLを更新しました。\n```\n{proxy_url}\n```",
                color=discord.Color.green()
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(PaypayCog(bot))

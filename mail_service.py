from __future__ import annotations

import html
import os
import secrets
import string
from datetime import datetime, timezone

from persistent_store import load_json_store, save_json_store

MAIL_DOMAIN = os.environ.get("MAIL_DOMAIN", "vel0x0.xyz").lower().strip()
MAIL_STORE_SHEET = "mail_accounts"
MAIL_STORE_FILE = "mail_accounts.json"


def load_accounts() -> dict:
    return load_json_store(MAIL_STORE_SHEET, MAIL_STORE_FILE)


def save_accounts(accounts: dict) -> None:
    save_json_store(MAIL_STORE_SHEET, accounts, MAIL_STORE_FILE)


def issue_address(user_id: int) -> str:
    accounts = load_accounts()
    alphabet = string.ascii_lowercase + string.digits
    while True:
        local = "".join(secrets.choice(alphabet) for _ in range(10))
        address = f"{local}@{MAIL_DOMAIN}"
        if address not in accounts:
            break
    accounts[address] = {
        "user_id": str(user_id),
        "display_deleted": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    save_accounts(accounts)
    return address


def hide_address(address: str, user_id: int) -> bool:
    accounts = load_accounts()
    info = accounts.get(address)
    if not info or str(info.get("user_id")) != str(user_id):
        return False
    info["display_deleted"] = True
    info["hidden_at"] = datetime.now(timezone.utc).isoformat()
    accounts[address] = info
    save_accounts(accounts)
    return True


def resolve_destination(address: str) -> str:
    info = load_accounts().get(address.lower().strip())
    if info and not info.get("display_deleted", False):
        return "inbox"
    return "archive"


def clean_text(value: str, limit: int = 3500) -> str:
    value = html.unescape(value or "").replace("\x00", "").strip()
    return value[:limit] + ("\n…（長すぎるため省略）" if len(value) > limit else "")


async def deliver_incoming(bot, payload: dict) -> None:
    import discord

    recipient = str(payload.get("to", "")).lower().strip()
    sender = clean_text(str(payload.get("from", "不明")), 500)
    subject = clean_text(str(payload.get("subject", "（件名なし）")), 500)
    body = clean_text(str(payload.get("text") or payload.get("body") or "（本文なし）"))
    destination = resolve_destination(recipient)
    env_name = "MAIL_INBOX_CHANNEL_ID" if destination == "inbox" else "MAIL_ARCHIVE_CHANNEL_ID"
    channel_id = int(os.environ.get(env_name, "0"))
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        channel = await bot.fetch_channel(channel_id)
    if channel is None:
        raise RuntimeError(f"{env_name} が未設定、またはチャンネルが見つかりません")

    embed = discord.Embed(
        title="📨 メール受信" if destination == "inbox" else "📦 メール保管（表示削除後）",
        color=discord.Color.blue() if destination == "inbox" else discord.Color.dark_grey(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="宛先", value=f"`{recipient or '不明'}`", inline=False)
    embed.add_field(name="送信者", value=f"`{sender}`", inline=False)
    embed.add_field(name="件名", value=f"`{subject}`", inline=False)
    embed.add_field(name="本文", value=f"```\n{body}\n```", inline=False)
    message_id = clean_text(str(payload.get("message_id", "")), 200)
    if message_id:
        embed.set_footer(text=f"Message-ID: {message_id}")
    await channel.send(embed=embed)

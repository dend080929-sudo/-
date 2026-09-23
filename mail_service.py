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


def get_account(address: str) -> dict | None:
    return load_accounts().get(address.lower().strip())


def get_current_address(user_id: int) -> str | None:
    accounts = load_accounts()
    candidates = [
        (address, info)
        for address, info in accounts.items()
        if str(info.get("user_id")) == str(user_id) and not info.get("display_deleted", False)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[1].get("created_at", ""), reverse=True)
    return candidates[0][0]


def get_panel_info(user_id: int) -> dict:
    info = load_accounts().get(f"__panel__:{user_id}", {})
    return info if isinstance(info, dict) else {}


def save_panel_info(user_id: int, channel_id: int, message_id: int) -> None:
    accounts = load_accounts()
    accounts[f"__panel__:{user_id}"] = {
        "channel_id": str(channel_id),
        "message_id": str(message_id),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    save_accounts(accounts)


def issue_address(user_id: int) -> str:
    accounts = load_accounts()
    # 再発行時は、以前の表示を隠し、以前のアドレスへの新着を保管側へ送る
    for address, info in accounts.items():
        if not address.startswith("__panel__:") and str(info.get("user_id")) == str(user_id):
            info["display_deleted"] = True
            info["hidden_at"] = datetime.now(timezone.utc).isoformat()

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


def hide_current_address(user_id: int) -> str | None:
    address = get_current_address(user_id)
    if not address:
        return None
    accounts = load_accounts()
    accounts[address]["display_deleted"] = True
    accounts[address]["hidden_at"] = datetime.now(timezone.utc).isoformat()
    save_accounts(accounts)
    return address


def resolve_destination(address: str) -> str:
    info = get_account(address)
    if info and not info.get("display_deleted", False):
        return "inbox"
    return "archive"


def clean_text(value: str, limit: int = 3500) -> str:
    value = html.unescape(value or "").replace("\x00", "").strip()
    return value[:limit] + ("\n…（長すぎるため省略）" if len(value) > limit else "")


async def deliver_incoming(bot, payload: dict) -> None:
    import discord

    recipient = str(payload.get("to", "")).lower().strip()
    info = get_account(recipient)
    destination = resolve_destination(recipient)
    sender = clean_text(str(payload.get("from", "不明")), 500)
    subject = clean_text(str(payload.get("subject", "（件名なし）")), 500)
    body = clean_text(str(payload.get("text") or payload.get("body") or "（本文なし）"))

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

    if destination == "inbox" and info and info.get("user_id"):
        panel = get_panel_info(int(info["user_id"]))
        channel_id = int(panel.get("channel_id", "0"))
    else:
        channel_id = 0

    env_name = "MAIL_INBOX_CHANNEL_ID" if destination == "inbox" else "MAIL_ARCHIVE_CHANNEL_ID"
    if not channel_id:
        channel_id = int(os.environ.get(env_name, "0"))
    channel = bot.get_channel(channel_id) if channel_id else None
    if channel is None and channel_id:
        channel = await bot.fetch_channel(channel_id)
    if channel is None:
        raise RuntimeError(f"{env_name} が未設定、またはチャンネルが見つかりません")
    await channel.send(embed=embed)

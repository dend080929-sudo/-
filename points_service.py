from __future__ import annotations

import copy
import secrets
import string
import threading
import time
from datetime import datetime, timezone

from persistent_store import load_json_store, save_json_store


POINTS_SHEET = "points_and_referrals"
POINTS_FILE = "points_and_referrals.json"
_LOCK = threading.RLock()

# 環境変数を増やさず、必要なら管理者コマンドで変更できる初期値。
DEFAULT_CONFIG = {
    "purchase_points_per_100_yen": 1,
    "inviter_reward": 100,
    "invitee_reward": 50,
    "minimum_account_age_days": 7,
    "minimum_guild_membership_days": 1,
}


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts or _now(), tz=timezone.utc).isoformat()


def _new_code(existing: set[str]) -> str:
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(100):
        code = "AVEL-" + "".join(secrets.choice(alphabet) for _ in range(8))
        if code not in existing:
            return code
    raise RuntimeError("招待コードを発行できませんでした")


def _load() -> dict:
    data = load_json_store(POINTS_SHEET, POINTS_FILE)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("config", copy.deepcopy(DEFAULT_CONFIG))
    for key, value in DEFAULT_CONFIG.items():
        data["config"].setdefault(key, value)
    data.setdefault("users", {})
    data.setdefault("referrals", {})
    data.setdefault("transactions", {})
    data.setdefault("suspicious", {})
    return data


def _save(data: dict) -> None:
    save_json_store(POINTS_SHEET, data, POINTS_FILE)


def _user(data: dict, user_id: int | str) -> dict:
    key = str(user_id)
    record = data["users"].setdefault(key, {})
    record.setdefault("points", 0)
    record.setdefault("created_at", _iso())
    record.setdefault("invite_code", "")
    record.setdefault("referred_by", "")
    record.setdefault("referral_status", "none")
    record.setdefault("history", [])
    return record


def ensure_invite_code(user_id: int | str) -> str:
    with _LOCK:
        data = _load()
        record = _user(data, user_id)
        if not record.get("invite_code"):
            existing = {str(v.get("invite_code")) for v in data["users"].values() if v.get("invite_code")}
            record["invite_code"] = _new_code(existing)
            _save(data)
        return str(record["invite_code"])


def get_balance(user_id: int | str) -> int:
    with _LOCK:
        return int(_user(_load(), user_id).get("points", 0))


def get_history(user_id: int | str, limit: int = 10) -> list[dict]:
    with _LOCK:
        history = _user(_load(), user_id).get("history", [])
        return list(reversed(history[-max(1, min(limit, 50)):]))


def _credit(data: dict, user_id: int | str, amount: int, reason: str, transaction_id: str) -> bool:
    if amount <= 0:
        return False
    if transaction_id in data["transactions"]:
        return False
    record = _user(data, user_id)
    record["points"] = int(record.get("points", 0)) + int(amount)
    record["history"].append({
        "at": _iso(), "amount": int(amount), "reason": reason,
        "transaction_id": transaction_id,
    })
    data["transactions"][transaction_id] = {
        "user_id": str(user_id), "amount": int(amount), "reason": reason, "at": _iso()
    }
    # 履歴肥大化を防ぎ、Sheetsのデータを必要最小限に保つ。
    record["history"] = record["history"][-100:]
    return True


def award_purchase_points(user_id: int | str, amount_yen: int, purchase_id: str) -> int:
    """成功済み購入に一度だけポイントを付与し、付与後の残高を返す。"""
    with _LOCK:
        data = _load()
        units = max(0, int(amount_yen)) // 100
        points = units * int(data["config"].get("purchase_points_per_100_yen", 1))
        if points:
            _credit(data, user_id, points, "商品購入", f"purchase:{purchase_id}")
        _save(data)
        return int(_user(data, user_id)["points"])


def reserve_points(user_id: int | str, amount: int, reservation_id: str) -> bool:
    """購入処理中だけポイントを仮確保する。二重確保と残高不足を原子的に防ぐ。"""
    amount = int(amount)
    if amount <= 0:
        return True
    with _LOCK:
        data = _load()
        if reservation_id in data["transactions"]:
            return data["transactions"][reservation_id].get("status") in {"reserved", "committed"}
        record = _user(data, user_id)
        if int(record.get("points", 0)) < amount:
            return False
        record["points"] = int(record["points"]) - amount
        record["history"].append({
            "at": _iso(), "amount": -amount, "reason": "値引きポイント仮確保",
            "transaction_id": reservation_id,
        })
        record["history"] = record["history"][-100:]
        data["transactions"][reservation_id] = {
            "user_id": str(user_id), "amount": amount, "reason": "ポイント値引き",
            "status": "reserved", "at": _iso(),
        }
        _save(data)
        return True


def commit_reserved_points(reservation_id: str) -> bool:
    with _LOCK:
        data = _load()
        tx = data["transactions"].get(reservation_id)
        if not tx:
            return False
        if tx.get("status") == "committed":
            return True
        if tx.get("status") != "reserved":
            return False
        tx["status"] = "committed"
        tx["committed_at"] = _iso()
        _save(data)
        return True


def release_reserved_points(reservation_id: str) -> bool:
    """決済・在庫処理失敗時に仮確保分を一度だけ返却する。"""
    with _LOCK:
        data = _load()
        tx = data["transactions"].get(reservation_id)
        if not tx:
            return False
        if tx.get("status") == "released":
            return True
        if tx.get("status") != "reserved":
            return False
        amount = int(tx.get("amount", 0))
        record = _user(data, tx.get("user_id", ""))
        record["points"] = int(record.get("points", 0)) + amount
        record["history"].append({
            "at": _iso(), "amount": amount, "reason": "決済失敗による値引きポイント返却",
            "transaction_id": f"{reservation_id}:release",
        })
        record["history"] = record["history"][-100:]
        tx["status"] = "released"
        tx["released_at"] = _iso()
        _save(data)
        return True


def record_purchase(user_id: int | str, *, purchase_id: str, guild_id: int | str,
                    product_name: str, quantity: int, paid_yen: int,
                    points_used: int, payment_method: str) -> bool:
    """成功した購入を一度だけ記録する。パスワード等の商品本文は保存しない。"""
    with _LOCK:
        data = _load()
        record = _user(data, user_id)
        transaction_id = f"purchase-record:{purchase_id}"
        if transaction_id in data["transactions"]:
            return False
        purchase = {
            "purchase_id": str(purchase_id),
            "guild_id": str(guild_id),
            "product_name": str(product_name)[:100],
            "quantity": max(1, int(quantity)),
            "paid_yen": max(0, int(paid_yen)),
            "points_used": max(0, int(points_used)),
            "payment_method": str(payment_method).upper()[:20],
            "at": _iso(),
        }
        record.setdefault("purchases", []).append(purchase)
        record["purchases"] = record["purchases"][-100:]
        record["purchase_count_total"] = int(record.get("purchase_count_total", 0)) + 1
        record["total_paid_yen"] = int(record.get("total_paid_yen", 0)) + purchase["paid_yen"]
        record["total_points_used"] = int(record.get("total_points_used", 0)) + purchase["points_used"]
        data["transactions"][transaction_id] = {
            "user_id": str(user_id), "reason": "購入履歴", "status": "recorded", "at": _iso()
        }
        _save(data)
        return True


def get_user_info(user_id: int | str) -> dict:
    with _LOCK:
        data = _load()
        record = copy.deepcopy(_user(data, user_id))
        purchases = record.get("purchases", [])
        record["purchase_count"] = int(record.get("purchase_count_total", len(purchases)))
        record["total_paid_yen"] = int(record.get("total_paid_yen", sum(int(row.get("paid_yen", 0)) for row in purchases)))
        record["total_points_used"] = int(record.get("total_points_used", sum(int(row.get("points_used", 0)) for row in purchases)))
        return record


def register_referral(invitee_id: int | str, code: str, *, account_created_at: float | None = None,
                      guild_joined_at: float | None = None) -> tuple[bool, str, int]:
    """招待を一度だけ成立させる。Discord IDだけでは同一人物判定はできないため保守的に拒否する。"""
    invitee_key = str(invitee_id)
    normalized = str(code or "").strip().upper()
    with _LOCK:
        data = _load()
        invitee = _user(data, invitee_key)
        if invitee.get("referred_by") or invitee.get("referral_status") in {"accepted", "pending", "rejected"}:
            return False, "このアカウントは、すでに招待登録を処理済みです。", int(invitee["points"])
        inviter_key = next((uid for uid, rec in data["users"].items() if str(rec.get("invite_code", "")).upper() == normalized), None)
        if not inviter_key:
            return False, "招待コードが見つかりません。", int(invitee["points"])
        if inviter_key == invitee_key:
            return False, "自分自身の招待コードは登録できません。", int(invitee["points"])

        config = data["config"]
        now = _now()
        if account_created_at and now - float(account_created_at) < int(config["minimum_account_age_days"]) * 86400:
            data["suspicious"][invitee_key] = {"reason": "アカウント作成直後", "at": _iso()}
            _save(data)
            return False, f"アカウント作成から{config['minimum_account_age_days']}日以上経過してから登録してください。", int(invitee["points"])
        if guild_joined_at and now - float(guild_joined_at) < int(config["minimum_guild_membership_days"]) * 86400:
            data["suspicious"][invitee_key] = {"reason": "サーバー参加直後", "at": _iso()}
            _save(data)
            return False, f"サーバー参加から{config['minimum_guild_membership_days']}日以上経過してから登録してください。", int(invitee["points"])

        # 招待コードは複数の友達に利用可能。招待される側は一人一回で、
        # 保存前に全条件を検証しているため同じ登録への二重付与はない。
        referral_id = f"referral:{inviter_key}:{invitee_key}"
        if referral_id in data["referrals"] or any(str(r.get("invitee_id")) == invitee_key for r in data["referrals"].values()):
            return False, "この招待はすでに処理されています。", int(invitee["points"])
        inviter = _user(data, inviter_key)
        if inviter.get("referral_status") == "rejected":
            return False, "この招待元は現在利用できません。", int(invitee["points"])
        inviter_amount = int(config.get("inviter_reward", 100))
        invitee_amount = int(config.get("invitee_reward", 50))
        _credit(data, inviter_key, inviter_amount, "友達招待報酬", f"{referral_id}:inviter")
        _credit(data, invitee_key, invitee_amount, "招待登録報酬", f"{referral_id}:invitee")
        invitee["referred_by"] = inviter_key
        invitee["referral_status"] = "accepted"
        data["referrals"][referral_id] = {
            "inviter_id": inviter_key, "invitee_id": invitee_key,
            "at": _iso(), "status": "accepted",
        }
        _save(data)
        return True, f"招待登録が完了しました。あなたに{invitee_amount}ポイント、招待者に{inviter_amount}ポイントを付与しました。", int(invitee["points"])


def admin_adjust(user_id: int | str, amount: int, reason: str) -> int:
    with _LOCK:
        data = _load()
        record = _user(data, user_id)
        transaction_id = f"admin:{user_id}:{time.time_ns()}"
        if amount > 0:
            _credit(data, user_id, amount, reason[:100], transaction_id)
        else:
            record["points"] = max(0, int(record["points"]) + int(amount))
            record["history"].append({"at": _iso(), "amount": int(amount), "reason": reason[:100], "transaction_id": transaction_id})
            record["history"] = record["history"][-100:]
        _save(data)
        return int(record["points"])


def leaderboard(limit: int = 10) -> list[tuple[str, int]]:
    with _LOCK:
        data = _load()
        rows = [(uid, int(rec.get("points", 0))) for uid, rec in data["users"].items()]
        return sorted(rows, key=lambda row: row[1], reverse=True)[:limit]

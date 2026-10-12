from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


INITIAL_FREE_RUNS = 3
RESERVATION_TTL_SECONDS = 2 * 60 * 60
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
SITE_ID_RE = re.compile(r"^[A-Z0-9]{16}$")


class BillingError(Exception):
    """A safe billing-storage or identity error."""


class BillingIdentityConflict(BillingError):
    """The IP, browser fingerprint, and site account point to different users."""


class BillingPersistenceError(sqlite3.OperationalError):
    """The durable Google Sheets snapshot could not be read or written."""


class AutocatBillingStore:
    """Atomic local ledger mirrored to row-based Google Sheets snapshots."""

    def __init__(self, db_path: str, secret: bytes | str):
        self.db_path = os.path.abspath(db_path)
        if isinstance(secret, str):
            secret = secret.encode("utf-8")
        if not secret:
            raise ValueError("billing identity secret is required")
        self._secret = bytes(secret)
        raw_sheet_name = os.getenv("AUTOCAT_BILLING_SHEET", "autocat_billing").strip()
        self._sheet_base = re.sub(r"[^A-Za-z0-9_-]", "_", raw_sheet_name)[:70] or "autocat_billing"
        self._sheets_enabled = bool(os.getenv("GOOGLE_CREDENTIALS_JSON"))
        self._stable_identity_secret = bool(os.getenv("IDENTITY_HASH_KEY") or os.getenv("FLASK_SECRET_KEY"))
        self._persistent_required = bool(os.getenv("RENDER_EXTERNAL_URL")) and (
            not self._sheets_enabled or not self._stable_identity_secret
        )
        self._storage_error = ""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialise()
        if self._sheets_enabled:
            self._restore_from_sheets()
        try:
            os.chmod(Path(self.db_path).parent, 0o700)
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _assert_storage_ready(self) -> None:
        if self._storage_error:
            raise BillingPersistenceError("課金記録の永続ストレージを利用できません。")
        if self._persistent_required and not self._sheets_enabled:
            raise BillingPersistenceError("Render環境で課金記録を永続化するGoogle Sheets設定がありません。")
        if self._persistent_required and not self._stable_identity_secret:
            raise BillingPersistenceError("Render環境で利用者識別に使う固定の秘密鍵が設定されていません。")

    @contextmanager
    def _transaction(self, persist: bool = True):
        self._assert_storage_ready()
        conn = self._connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            if persist and self._sheets_enabled:
                self._sync_to_sheets(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialise(self) -> None:
        with self._connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS billing_identities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    first_free_runs_used INTEGER NOT NULL DEFAULT 0
                        CHECK(first_free_runs_used >= 0),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS billing_identity_keys (
                    kind TEXT NOT NULL CHECK(kind IN ('ip','fingerprint','site')),
                    key_hash TEXT NOT NULL,
                    identity_id INTEGER NOT NULL REFERENCES billing_identities(id) ON DELETE CASCADE,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(kind,key_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_billing_identity_id
                    ON billing_identity_keys(identity_id);
                CREATE TABLE IF NOT EXISTS free_run_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    identity_id INTEGER NOT NULL REFERENCES billing_identities(id) ON DELETE CASCADE,
                    status TEXT NOT NULL CHECK(status IN ('reserved','consumed','refunded','expired_refunded')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_free_run_identity_status
                    ON free_run_reservations(identity_id,status);
                CREATE TABLE IF NOT EXISTS paypay_link_claims (
                    link_hash TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('processing','received','review')),
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paypay_refund_requests (
                    job_id TEXT PRIMARY KEY,
                    operation_id TEXT NOT NULL DEFAULT '',
                    site_public_id TEXT NOT NULL DEFAULT '',
                    site_username TEXT NOT NULL DEFAULT '',
                    discord_user_id TEXT NOT NULL DEFAULT '',
                    amount_yen INTEGER NOT NULL CHECK(amount_yen > 0),
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','refunded','cancelled')),
                    created_at REAL NOT NULL,
                    notified_at REAL,
                    notification_attempts INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_paypay_refund_status
                    ON paypay_refund_requests(status,created_at);
            """)

    @staticmethod
    def _table_columns() -> dict[str, tuple[str, ...]]:
        return {
            "billing_identities": ("id", "first_free_runs_used", "created_at", "updated_at"),
            "billing_identity_keys": ("kind", "key_hash", "identity_id", "created_at"),
            "free_run_reservations": ("reservation_id", "identity_id", "status", "created_at", "updated_at"),
            "paypay_link_claims": ("link_hash", "payment_id", "status", "created_at", "updated_at"),
            "paypay_refund_requests": (
                "job_id", "operation_id", "site_public_id", "site_username", "discord_user_id",
                "amount_yen", "reason", "status", "created_at", "notified_at", "notification_attempts",
            ),
        }

    def _sheet_objects(self):
        try:
            from persistent_store import _client
            client = _client()
            if client is None:
                raise BillingPersistenceError("Google Sheets credentials are not configured.")
            book = client.open(os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB"))
            return book
        except BillingPersistenceError:
            raise
        except Exception as exc:
            raise BillingPersistenceError("Google Sheets connection failed.") from exc

    def _worksheet(self, book, title: str, rows: int = 100, cols: int = 3):
        try:
            return book.worksheet(title)
        except Exception as exc:
            try:
                import gspread
                if not isinstance(exc, gspread.exceptions.WorksheetNotFound):
                    raise
            except ImportError:
                raise
            return book.add_worksheet(title=title, rows=rows, cols=cols)

    def _restore_from_sheets(self) -> None:
        try:
            book = self._sheet_objects()
            meta = self._worksheet(book, f"{self._sheet_base}_meta", 10, 2)
            active = str(meta.acell("A1").value or "").strip().lower()
            if active not in {"a", "b"}:
                return
            sheet = self._worksheet(book, f"{self._sheet_base}_{active}", 100, 3)
            values = sheet.get_all_values()
            rows: dict[str, list[dict]] = {name: [] for name in self._table_columns()}
            for row in values[1:]:
                if len(row) < 3 or row[0] != "1" or row[1] not in rows:
                    continue
                record = json.loads(row[2])
                if isinstance(record, dict):
                    rows[row[1]].append(record)
            conn = self._connect(self.db_path)
            try:
                conn.execute("BEGIN IMMEDIATE")
                for table in ("paypay_refund_requests", "paypay_link_claims", "free_run_reservations", "billing_identity_keys", "billing_identities"):
                    conn.execute(f"DELETE FROM {table}")
                for table, columns in self._table_columns().items():
                    placeholders = ",".join("?" for _ in columns)
                    names = ",".join(columns)
                    for record in rows[table]:
                        conn.execute(
                            f"INSERT OR REPLACE INTO {table}({names}) VALUES({placeholders})",
                            tuple(record.get(column) for column in columns),
                        )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as exc:
            self._storage_error = type(exc).__name__
            print(f"[AUTOCAT BILLING] durable ledger restore failed: {type(exc).__name__}")

    def _sync_to_sheets(self, conn: sqlite3.Connection) -> None:
        book = self._sheet_objects()
        meta = self._worksheet(book, f"{self._sheet_base}_meta", 10, 2)
        current = str(meta.acell("A1").value or "").strip().lower()
        target = "b" if current == "a" else "a"
        sheet = self._worksheet(book, f"{self._sheet_base}_{target}", 100, 3)
        values = [["snapshot_version", "table", "record_json"]]
        for table, columns in self._table_columns().items():
            for row in conn.execute(f"SELECT {','.join(columns)} FROM {table}"):
                values.append(["1", table, json.dumps(dict(row), ensure_ascii=False, separators=(",", ":"))])
        try:
            sheet.clear()
            if sheet.row_count < len(values) or sheet.col_count < 3:
                sheet.resize(rows=max(100, len(values)), cols=3)
            for offset in range(0, len(values), 500):
                sheet.update(values=values[offset:offset + 500], range_name=f"A{offset + 1}")
            meta.update_acell("A1", target)
        except Exception as exc:
            raise BillingPersistenceError("Google Sheets durable ledger update failed.") from exc

    def _digest(self, kind: str, value: str) -> str:
        return hmac.new(
            self._secret,
            f"autocat-billing:{kind}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _normalise_ip(raw_ip: object) -> str:
        try:
            return ipaddress.ip_address(str(raw_ip or "").strip()).compressed
        except ValueError as exc:
            raise BillingError("接続元IPを確認できませんでした。") from exc

    @staticmethod
    def _normalise_fingerprint(raw_fingerprint: object) -> str:
        value = str(raw_fingerprint or "").strip().lower()
        if not FINGERPRINT_RE.fullmatch(value):
            raise BillingError("端末情報を確認できませんでした。ページを再読み込みしてください。")
        return value

    def _identity_keys(self, raw_ip: object, raw_fingerprint: object, discord_user_id: object = ""):
        # Discord OAuth identity is the source of truth for first-use quotas.
        # Do not bind quotas to IP/device: families and shared networks must not
        # merge separate Discord users into one account or create conflicts.
        user_id = str(discord_user_id or "").strip()
        if not re.fullmatch(r"\d{15,22}", user_id):
            raise BillingError("Discordログインを確認できませんでした。")
        return [("site", self._digest("site", f"discord:{user_id}"))]

    @staticmethod
    def _release_stale_locked(conn: sqlite3.Connection, now: float) -> bool:
        cur = conn.execute(
            "UPDATE free_run_reservations SET status='expired_refunded',updated_at=? "
            "WHERE status='reserved' AND created_at<?",
            (now, now - RESERVATION_TTL_SECONDS),
        )
        return cur.rowcount > 0

    def _resolve_identity_locked(self, conn: sqlite3.Connection, keys, now: float) -> tuple[int, bool]:
        owners = set()
        for kind, key_hash in keys:
            row = conn.execute(
                "SELECT identity_id FROM billing_identity_keys WHERE kind=? AND key_hash=?",
                (kind, key_hash),
            ).fetchone()
            if row:
                owners.add(int(row[0]))
        if len(owners) > 1:
            raise BillingIdentityConflict(
                "IP・端末・サイトアカウントの利用記録が一致しません。"
                "同じ端末とサイトアカウントで再度お試しください。"
            )
        changed = False
        if owners:
            identity_id = owners.pop()
        else:
            cur = conn.execute(
                "INSERT INTO billing_identities(first_free_runs_used,created_at,updated_at) VALUES(0,?,?)",
                (now, now),
            )
            identity_id = int(cur.lastrowid)
            changed = True
        for kind, key_hash in keys:
            cur = conn.execute(
                "INSERT OR IGNORE INTO billing_identity_keys(kind,key_hash,identity_id,created_at) VALUES(?,?,?,?)",
                (kind, key_hash, identity_id, now),
            )
            changed = changed or cur.rowcount > 0
            owner = conn.execute(
                "SELECT identity_id FROM billing_identity_keys WHERE kind=? AND key_hash=?",
                (kind, key_hash),
            ).fetchone()
            if not owner or int(owner[0]) != identity_id:
                raise BillingIdentityConflict("この端末または回線は別の利用記録に紐づいています。")
        if changed:
            conn.execute("UPDATE billing_identities SET updated_at=? WHERE id=?", (now, identity_id))
        return identity_id, changed

    def status(self, raw_ip: object, raw_fingerprint: object, discord_user_id: object = "") -> dict:
        now = time.time()
        keys = self._identity_keys(raw_ip, raw_fingerprint, discord_user_id)
        with self._transaction(persist=False) as conn:
            changed = self._release_stale_locked(conn, now)
            identity_id, identity_changed = self._resolve_identity_locked(conn, keys, now)
            row = conn.execute(
                "SELECT first_free_runs_used FROM billing_identities WHERE id=?",
                (identity_id,),
            ).fetchone()
            reserved = int(conn.execute(
                "SELECT COUNT(*) FROM free_run_reservations WHERE identity_id=? AND status='reserved'",
                (identity_id,),
            ).fetchone()[0])
            used = int(row[0]) if row else 0
            if (changed or identity_changed) and self._sheets_enabled:
                self._sync_to_sheets(conn)
        return {
            "free_runs_limit": INITIAL_FREE_RUNS,
            "free_runs_used": used,
            "free_runs_reserved": reserved,
            "remaining": max(0, INITIAL_FREE_RUNS - used - reserved),
            "unlimited": False,
        }

    def reserve_free_run(
        self,
        raw_ip: object,
        raw_fingerprint: object,
        reservation_id: str,
        discord_user_id: object = "",
    ) -> dict | None:
        reservation_id = str(reservation_id or "").strip()
        if not reservation_id or len(reservation_id) > 160:
            raise BillingError("利用予約IDが不正です。")
        now = time.time()
        keys = self._identity_keys(raw_ip, raw_fingerprint, discord_user_id)
        with self._transaction() as conn:
            self._release_stale_locked(conn, now)
            existing = conn.execute(
                "SELECT identity_id,status FROM free_run_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone()
            if existing:
                return {"reservation_id": reservation_id, "status": str(existing[1]), "free": True}
            identity_id, _identity_changed = self._resolve_identity_locked(conn, keys, now)
            row = conn.execute(
                "SELECT first_free_runs_used FROM billing_identities WHERE id=?",
                (identity_id,),
            ).fetchone()
            used = int(row[0]) if row else 0
            reserved = int(conn.execute(
                "SELECT COUNT(*) FROM free_run_reservations WHERE identity_id=? AND status='reserved'",
                (identity_id,),
            ).fetchone()[0])
            if used + reserved >= INITIAL_FREE_RUNS:
                return None
            conn.execute(
                "INSERT INTO free_run_reservations(reservation_id,identity_id,status,created_at,updated_at) "
                "VALUES(?,?,'reserved',?,?)",
                (reservation_id, identity_id, now, now),
            )
            return {
                "reservation_id": reservation_id,
                "status": "reserved",
                "free": True,
                "remaining": max(0, INITIAL_FREE_RUNS - used - reserved - 1),
            }

    def settle_free_run(self, reservation_id: str, success: bool) -> bool:
        key = str(reservation_id or "").strip()
        if not key:
            return False
        now = time.time()
        with self._transaction(persist=False) as conn:
            row = conn.execute(
                "SELECT identity_id,status FROM free_run_reservations WHERE reservation_id=?",
                (key,),
            ).fetchone()
            if not row:
                return False
            identity_id, status = int(row[0]), str(row[1])
            target = "consumed" if success else "refunded"
            if status == target:
                return True
            if status != "reserved":
                return False
            cur = conn.execute(
                "UPDATE free_run_reservations SET status=?,updated_at=? WHERE reservation_id=? AND status='reserved'",
                (target, now, key),
            )
            if cur.rowcount != 1:
                return False
            if success:
                conn.execute(
                    "UPDATE billing_identities SET first_free_runs_used=first_free_runs_used+1,updated_at=? WHERE id=?",
                    (now, identity_id),
                )
            if self._sheets_enabled:
                self._sync_to_sheets(conn)
            return True

    def payment_link_hash(self, link: str) -> str:
        return self._digest("paypay-link", str(link or "").strip())

    def claim_paypay_link(self, link_hash: str, payment_id: str) -> bool:
        now = time.time()
        try:
            with self._transaction() as conn:
                conn.execute(
                    "INSERT INTO paypay_link_claims(link_hash,payment_id,status,created_at,updated_at) "
                    "VALUES(?,?,'processing',?,?)",
                    (str(link_hash), str(payment_id), now, now),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def set_paypay_link_status(self, link_hash: str, status: str) -> bool:
        if status not in {"received", "review"}:
            raise ValueError("invalid PayPay link state")
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "UPDATE paypay_link_claims SET status=?,updated_at=? WHERE link_hash=?",
                (status, time.time(), str(link_hash)),
            )
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
            return cur.rowcount == 1

    def release_paypay_link(self, link_hash: str) -> bool:
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "DELETE FROM paypay_link_claims WHERE link_hash=? AND status='processing'",
                (str(link_hash),),
            )
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
            return cur.rowcount == 1

    def create_refund_request(
        self,
        *,
        job_id: str,
        operation_id: str = "",
        site_public_id: str = "",
        site_username: str = "",
        discord_user_id: str = "",
        amount_yen: int,
        reason: str,
    ) -> tuple[dict, bool]:
        now = time.time()
        amount = int(amount_yen)
        if amount <= 0:
            raise ValueError("refund amount must be positive")
        values = (
            str(job_id)[:160], str(operation_id or "")[:160], str(site_public_id or "")[:32],
            str(site_username or "")[:100], str(discord_user_id or "")[:32], amount,
            str(reason or "処理失敗")[:1000], now,
        )
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO paypay_refund_requests(job_id,operation_id,site_public_id,site_username,discord_user_id,amount_yen,reason,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                values,
            )
            row = conn.execute(
                "SELECT job_id,operation_id,site_public_id,site_username,discord_user_id,amount_yen,reason,status,created_at,notified_at,notification_attempts "
                "FROM paypay_refund_requests WHERE job_id=?",
                (str(job_id)[:160],),
            ).fetchone()
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
        return dict(row) if row else {}, cur.rowcount == 1

    def claim_refund_notification(self, job_id: str, max_attempts: int = 3) -> bool:
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "UPDATE paypay_refund_requests SET notification_attempts=notification_attempts+1 "
                "WHERE job_id=? AND status='pending' AND notified_at IS NULL AND notification_attempts<?",
                (str(job_id)[:160], max(1, int(max_attempts))),
            )
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
            return cur.rowcount == 1

    def mark_refund_notified(self, job_id: str) -> bool:
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "UPDATE paypay_refund_requests SET notified_at=? WHERE job_id=? AND notified_at IS NULL",
                (time.time(), str(job_id)[:160]),
            )
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
            return cur.rowcount == 1

    def pending_refund_requests(self, limit: int = 50) -> list[dict]:
        self._assert_storage_ready()
        bounded = max(1, min(int(limit), 200))
        with self._connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT job_id,operation_id,site_public_id,site_username,discord_user_id,amount_yen,reason,status,created_at,notified_at "
                "FROM paypay_refund_requests WHERE status='pending' ORDER BY created_at LIMIT ?",
                (bounded,),
            ).fetchall()
        return [dict(row) for row in rows]

    def resolve_refund_request(self, job_id: str, status: str) -> bool:
        if status not in {"refunded", "cancelled"}:
            raise ValueError("invalid refund status")
        with self._transaction(persist=False) as conn:
            cur = conn.execute(
                "UPDATE paypay_refund_requests SET status=? WHERE job_id=? AND status='pending'",
                (status, str(job_id)[:160]),
            )
            if cur.rowcount == 1 and self._sheets_enabled:
                self._sync_to_sheets(conn)
            return cur.rowcount == 1

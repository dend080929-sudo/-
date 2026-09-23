import copy
import json
import os
import threading
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

_CACHE = {}
_CACHE_LOCK = threading.RLock()
_WRITE_LOCK = threading.Lock()


def _client():
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if not raw:
        return None
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    return gspread.authorize(credentials)


def _read_fallback(fallback_file: str) -> dict:
    path = Path(fallback_file)
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _read_sheet(sheet_name: str) -> dict:
    client = _client()
    if not client:
        return {}
    book = client.open(os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB"))
    try:
        sheet = book.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return {}
    value = sheet.cell(1, 1).value
    if not value:
        return {}
    decoded = json.loads(value)
    return decoded if isinstance(decoded, dict) else {}


def load_json_store(sheet_name: str, fallback_file: str) -> dict:
    with _CACHE_LOCK:
        if sheet_name in _CACHE:
            return copy.deepcopy(_CACHE[sheet_name])

    try:
        data = _read_sheet(sheet_name)
    except Exception as exc:
        print(f"スプレッドシート({sheet_name})読み込みエラー: {exc}")
        data = {}

    if not data:
        data = _read_fallback(fallback_file)

    with _CACHE_LOCK:
        _CACHE[sheet_name] = copy.deepcopy(data)
    return copy.deepcopy(data)


def _write_sheet(sheet_name: str, encoded: str) -> None:
    try:
        client = _client()
        if not client:
            return
        book = client.open(os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB"))
        try:
            sheet = book.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            sheet = book.add_worksheet(title=sheet_name, rows=10, cols=2)
        sheet.clear()
        sheet.update_cell(1, 1, encoded)
    except Exception as exc:
        print(f"スプレッドシート({sheet_name})保存エラー: {exc}")


def save_json_store(sheet_name: str, data: dict, fallback_file: str) -> None:
    encoded = json.dumps(data, ensure_ascii=False)
    with _CACHE_LOCK:
        _CACHE[sheet_name] = copy.deepcopy(data)
    # ローカルにも即時保存し、Render側の一時障害でも直近データを保持する
    try:
        Path(fallback_file).write_text(encoded, encoding="utf-8")
    except OSError:
        pass
    # 呼び出し側がDiscordへdefer済みなら、ここでシート保存完了まで待ってから
    # followupを返せる。保存完了前の再起動でデータが欠落するのを防ぐ。
    with _WRITE_LOCK:
        _write_sheet(sheet_name, encoded)


def preload_json_stores(stores: list[tuple[str, str]]) -> None:
    """Bot起動時にまとめて読み込み、コマンド実行中のGoogle API待ちを避ける。"""
    for sheet_name, fallback_file in stores:
        load_json_store(sheet_name, fallback_file)

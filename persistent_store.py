import json
import os
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials


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


def load_json_store(sheet_name: str, fallback_file: str) -> dict:
    try:
        client = _client()
        if client:
            book = client.open(os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB"))
            try:
                sheet = book.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                return {}
            value = sheet.cell(1, 1).value
            return json.loads(value) if value else {}
    except Exception as exc:
        print(f"スプレッドシート({sheet_name})読み込みエラー: {exc}")

    path = Path(fallback_file)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_json_store(sheet_name: str, data: dict, fallback_file: str) -> None:
    encoded = json.dumps(data, ensure_ascii=False)
    try:
        client = _client()
        if client:
            book = client.open(os.environ.get("SPREADSHEET_NAME", "DiscordVendingDB"))
            try:
                sheet = book.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                sheet = book.add_worksheet(title=sheet_name, rows=10, cols=2)
            sheet.clear()
            sheet.update_cell(1, 1, encoded)
            return
    except Exception as exc:
        print(f"スプレッドシート({sheet_name})保存エラー: {exc}")

    Path(fallback_file).write_text(encoded, encoding="utf-8")

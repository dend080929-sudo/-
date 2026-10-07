"""Register the Tsum-tsum command suite onto the existing Discord Bot instance."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent
_MODULES = (
    ("tsum_login", "tsum_login.py"),
    ("line_password_login", "line_password_login.py"),
    ("tsum", "tsum.py"),
    ("tsum_guest", "tsum_guest.py"),
    ("tsum_forge", "tsum_forge.py"),
    ("tsum_bot", "tsum_bot.py"),
)


def _prepare_runtime_config() -> Path:
    """Copy an optional read-only Render Secret File into writable app storage."""
    runtime_path = ROOT / "tsum_settings.json"
    configured = os.environ.get("TSUM_BOT_CONFIG", "").strip()
    if configured:
        source = Path(configured)
        if not source.is_absolute():
            source = ROOT / source
        source = source.resolve()
        if source != runtime_path.resolve():
            if not source.is_file():
                raise FileNotFoundError(f"TSUM_BOT_CONFIGが見つかりません: {source}")
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("ツムツム設定はJSONオブジェクトで指定してください")
            # Discord token is owned by the host app (DISCORD_BOT_TOKEN), never by this bundle.
            data.pop("token", None)
            runtime_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif not runtime_path.is_file():
        example = ROOT / "tsum_settings.example.json"
        if not example.is_file():
            raise FileNotFoundError(f"ツムツム設定テンプレートがありません: {example}")
        shutil.copyfile(example, runtime_path)

    # Remove any legacy second-Bot token from a pre-existing writable config too.
    data = json.loads(runtime_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("ツムツム設定はJSONオブジェクトで指定してください")
    if "token" in data:
        data.pop("token", None)
        runtime_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.environ["TSUM_BOT_CONFIG"] = str(runtime_path.resolve())
    return runtime_path


def _load_as(module_name: str, filename: str) -> ModuleType:
    source = ROOT / filename
    if not source.is_file():
        raise FileNotFoundError(f"ツムツム用ファイルが見つかりません: {source}")

    already = sys.modules.get(module_name)
    if already is not None:
        old_file = getattr(already, "__file__", None)
        if old_file and Path(old_file).resolve().parent == ROOT.resolve():
            return already
        raise ImportError(f"Pythonモジュール名が既存アプリと衝突しています: {module_name}")

    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise ImportError(f"ツムツム用モジュールを読み込めません: {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _command_root(interaction) -> str:
    command = getattr(interaction, "command", None)
    qualified = getattr(command, "qualified_name", "") or getattr(command, "name", "")
    return qualified.split(" ", 1)[0]


async def attach_tsum_bot(shared_bot) -> None:
    """Load Tsum commands, events and persistent views onto the host Bot."""
    if getattr(shared_bot, "_tsum_bundle_attached", False):
        return

    _prepare_runtime_config()
    root_text = str(ROOT)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)

    context = importlib.import_module("tsum_bot_context")
    context.bot = shared_bot

    existing_names = {command.name for command in shared_bot.tree.get_commands()}
    loaded = {}
    for module_name, filename in _MODULES:
        loaded[module_name] = _load_as(module_name, filename)

    login_module = loaded["tsum_login"]
    try:
        login_module.resolve_appver()
    except Exception as exc:
        print(f"[tsum] appver更新をスキップ: {type(exc).__name__}: {exc}")

    tsum_module = loaded["tsum_bot"]
    tsum_module.PAYPAY_HELPER = str(ROOT / "paypay_helper.py")
    tsum_module.PP_RELOGIN_START = str(ROOT / "_pp_relogin_start.py")
    tsum_module.PP_RELOGIN_OTP = str(ROOT / "_pp_relogin_otp.py")
    if tsum_module.bot is not shared_bot:
        raise RuntimeError("ツムツム機能が既存Botインスタンスを共有できませんでした")

    all_names = {command.name for command in shared_bot.tree.get_commands()}
    tsum_names = frozenset(all_names - existing_names)
    if not tsum_names:
        raise RuntimeError("ツムツムのスラッシュコマンドが登録されませんでした")
    tsum_module.TSUM_COMMAND_NAMES = tsum_names
    tsum_module.INTEGRATED_WITH_EXISTING_BOT = True

    # Preserve existing handlers: Discord.py has a single tree-level check/error hook.
    previous_check = shared_bot.tree.interaction_check

    async def combined_interaction_check(interaction):
        root_name = _command_root(interaction)
        if root_name in tsum_names and root_name not in tsum_module.TSUM_PUBLIC_COMMANDS:
            return await tsum_module._tree_admin_only(interaction)
        return await previous_check(interaction)

    shared_bot.tree.interaction_check = combined_interaction_check

    previous_error = shared_bot.tree.on_error

    async def combined_app_command_error(interaction, error):
        if _command_root(interaction) in tsum_names:
            await tsum_module._on_app_command_error(interaction, error)
        elif previous_error is not None:
            await previous_error(interaction, error)
        else:
            print(f"アプリコマンドエラー: {type(error).__name__}: {error}")

    shared_bot.tree.on_error = combined_app_command_error

    # Use listeners rather than @bot.event so the host app's handlers remain registered.
    shared_bot.add_listener(tsum_module.on_ready, "on_ready")
    shared_bot.add_listener(tsum_module.on_guild_join, "on_guild_join")

    configured_proxy = tsum_module._DISCORD_PROXY
    if configured_proxy:
        shared_bot.http.proxy = configured_proxy

    # Register persistent views and background tasks. Command syncing stays with main.py,
    # which syncs the combined tree exactly once using DISCORD_GUILD_ID.
    await tsum_module.TsumBot.setup_hook(shared_bot)
    setattr(shared_bot, "_tsum_bundle_attached", True)
    print(f"✅ ツムツム機能を既存Botへ統合しました（コマンド{len(tsum_names)}件）")

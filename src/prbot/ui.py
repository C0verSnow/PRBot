"""Native macOS dialogs, also available on Macs without a Python/Tk installation."""

import asyncio
import json
import subprocess

from prbot import service
from prbot.config import Config, ConfigError, keychain_token, validate_token
from prbot.main import validate_api


def applescript(source):
    result = subprocess.run(
        ["/usr/bin/osascript", "-"],
        input=source,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        return None  # Includes the user pressing Cancel.
    return result.stdout.strip()


def tell(message):
    applescript(
        f"display dialog {json.dumps(message, ensure_ascii=False)} "
        'with title "PRBot" buttons {"好"} default button "好"'
    )


def show():
    while True:
        action = applescript("""
set options to {"配置 Token", "启动服务", "停止服务", "重启服务", ¬
    "开启登录自启动", "关闭登录自启动", "查看状态"}
set choice to choose from list options with title "PRBot v0.1.0" ¬
    with prompt "首次使用请先配置 Token。关闭此界面不影响后台服务。"
if choice is false then return ""
return item 1 of choice
""")
        if not action:
            return
        try:
            if action == "配置 Token":
                value = applescript("""
text returned of (display dialog "请输入 BotFather 提供的 Token（存入 macOS 钥匙串）。" ¬
    default answer "" with hidden answer with title "PRBot")
""")
                if value is None:
                    continue
                token = validate_token(value.strip())
                asyncio.run(validate_api(token))
                keychain_token(token)
                tell("Token 已验证并保存，请启动或重启服务使新配置生效。")
            elif action == "停止服务":
                service.stop()
                tell("服务已停止，登录自启动也已移除。")
            elif action == "查看状态":
                tell(json.dumps(service.status(), ensure_ascii=False, indent=2))
            else:
                config = Config.load()
                asyncio.run(validate_api(config.token))
                auto = config.autostart
                if action == "开启登录自启动":
                    auto = True
                elif action == "关闭登录自启动":
                    auto = False
                service.install(auto)
                tell("后台服务已启动。请在 Telegram 向 Bot 发送 /id。")
        except ConfigError as error:
            tell(str(error))

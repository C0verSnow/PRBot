import argparse
import asyncio
import json
import logging
import os
import signal
import sys

from telegram import Bot
from telegram.error import TelegramError

from prbot import __version__, service
from prbot.config import Config, ConfigError, keychain_token, validate_token
from prbot.logging_config import configure
from prbot.state import State, instance_lock
from prbot.telegram_client import ServiceHalted, run


async def validate_api(token):
    try:
        async with Bot(token) as bot:
            return bot.username
    except TelegramError as error:
        raise ConfigError(
            f"Token 验证失败（{type(error).__name__}），请检查 Token 和网络。"
        ) from None


async def daemon(config):
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    state = State(config.token)
    try:
        await run(config, state, stop, service.report)
    finally:
        state.close()


def serve():
    configure("INFO")
    try:
        with instance_lock():
            try:
                config = Config.load()
                configure(config.log_level)
                logging.info("服务启动，配置加载成功")
                service.report("starting", "正在验证配置")
                asyncio.run(daemon(config))
            except (ConfigError, ServiceHalted) as error:
                logging.error("%s", error)
                service.report("halted", str(error))
                return 0  # Intentional halt: launchd must not restart bad credentials/conflicts.
            except Exception as error:
                logging.error("服务异常 type=%s", type(error).__name__)
                service.report("crashed", f"异常类型：{type(error).__name__}")
                return 1
            finally:
                logging.info("服务停止")
    except RuntimeError:
        logging.error("已有 PRBot 实例运行")
        return 0
    service.report("stopped", "服务已停止")
    return 0


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="PRBot macOS service")
    parser.add_argument(
        "command",
        nargs="?",
        default="ui",
        choices=[
            "ui",
            "run",
            "start",
            "stop",
            "restart",
            "status",
            "autostart",
            "configure",
            "version",
            "sdk-worker",
            "sdk-check",
            "import-codex",
        ],
    )
    parser.add_argument("value", nargs="?", choices=["on", "off"])
    args = parser.parse_args()
    try:
        if args.command == "sdk-worker":
            from prbot.sdk_worker import run as sdk_run

            sdk_run()
        elif args.command == "sdk-check":
            from codex_cli_bin import bundled_codex_path
            from openai_codex import Codex, CodexConfig

            with Codex(
                CodexConfig(env={"CODEX_HOME": str(service.data_dir() / "sdk-check")})
            ) as sdk:
                print(
                    json.dumps(
                        {
                            "runtime": str(bundled_codex_path()),
                            "initialized": sdk.metadata is not None,
                        }
                    )
                )
        elif args.command == "import-codex":
            from prbot.execution_setup import import_codex_login

            import_codex_login()
            print("现有 Codex 登录已导入钥匙串，请重启服务。")
        elif args.command == "version":
            print(__version__)
        elif args.command == "ui":
            from prbot.ui import show

            show()
        elif args.command == "configure":
            import getpass

            token = validate_token(getpass.getpass("Telegram Bot Token: ").strip())
            asyncio.run(validate_api(token))
            keychain_token(token)
            print("Token 已验证并保存到钥匙串。请重启服务使配置生效。")
        elif args.command == "run":
            return serve()
        elif args.command == "stop":
            service.stop()
            print("后台服务和登录自启动已停止；下次启动可重新启用。")
        elif args.command in {"start", "restart", "autostart"}:
            config = Config.load()
            if args.command == "autostart" and args.value is None:
                parser.error("autostart 需要 on 或 off")
            # Check credentials in the controller too, before registering launchd.
            asyncio.run(validate_api(config.token))
            automatic = (args.value == "on") if args.command == "autostart" else config.autostart
            service.install(automatic)
            print("后台服务已启动。")
        elif args.command == "status":
            print(json.dumps(service.status(), ensure_ascii=False, indent=2))
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

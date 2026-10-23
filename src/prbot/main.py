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
from prbot.config import Config, ConfigError
from prbot.logging_config import configure
from prbot.state import State, instance_lock
from prbot.telegram_client import ServiceHalted, run


async def validate_api(token):
    try:
        async with Bot(token) as bot:
            return bot.username
    except TelegramError as error:
        raise ConfigError(
            f"Telegram verification failed ({type(error).__name__}). Check Infisical and network."
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
                logging.info("Service starting; configuration loaded")
                service.report("starting", "Checking configuration")
                asyncio.run(daemon(config))
            except (ConfigError, ServiceHalted) as error:
                logging.error("%s", error)
                service.report("halted", str(error))
                return 0  # Intentional halt: launchd must not restart bad credentials/conflicts.
            except Exception as error:
                logging.error("Service failure type=%s", type(error).__name__)
                service.report("crashed", f"Failure type: {type(error).__name__}")
                return 1
            finally:
                logging.info("Service stopped")
    except RuntimeError:
        logging.error("PRBot is already running")
        return 0
    service.report("stopped", "Service stopped")
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
            "ui-check",
            "run",
            "start",
            "stop",
            "restart",
            "status",
            "version",
            "sdk-worker",
            "sdk-check",
            "provider-check",
        ],
    )
    args = parser.parse_args()
    try:
        if args.command == "sdk-worker":
            from prbot.sdk_worker import run as sdk_run

            sdk_run()
        elif args.command == "provider-check":
            from prbot.provider_check import run as provider_check

            provider_check()
        elif args.command == "sdk-check":
            from prbot.codex_adapter import CodexAdapter

            path = service.data_dir() / "sdk-check"
            path.mkdir(mode=0o700, exist_ok=True)
            result = asyncio.run(CodexAdapter("{}").invoke(path, "__prbot_runtime_check__", {}, 30))
            print(json.dumps(result))
        elif args.command == "version":
            print(__version__)
        elif args.command == "ui-check":
            from pathlib import Path

            from prbot.desktop import show

            if os.environ.get("CI") != "true" or not os.environ.get("PRBOT_UI_CHECK_OUTPUT"):
                raise ConfigError("UI checks are only allowed in remote CI.")
            output = Path(os.environ["PRBOT_UI_CHECK_OUTPUT"])
            # UI acceptance uses only synthetic credentials and a disposable home.
            import tempfile

            with tempfile.TemporaryDirectory(prefix="prbot-ui-") as test_home:
                os.environ["HOME"] = test_home
                show(check_output=output)
            if not json.loads(output.with_suffix(".json").read_text()).get("ok"):
                raise ConfigError("Packaged UI check failed. Inspect remote artifacts.")
        elif args.command == "ui":
            from prbot.ui import show

            show()
        elif args.command == "run":
            return serve()
        elif args.command == "stop":
            service.stop()
            print("Background service stopped.")
        elif args.command in {"start", "restart"}:
            config = Config.load()
            asyncio.run(validate_api(config.token))
            service.install()
            print("Starting background service; wait for its health report.")
        elif args.command == "status":
            print(json.dumps(service.status(), ensure_ascii=False, indent=2))
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

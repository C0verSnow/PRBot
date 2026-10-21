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


ACTIONS = {
    "打开配置目录",
    "选择工作路径",
    "配置任务",
    "导入 Codex 登录",
    "配置 Token",
    "启动服务",
    "停止服务",
    "重启服务",
    "开启登录自启动",
    "关闭登录自启动",
    "查看状态",
}


def perform_action(action):
    if action not in ACTIONS:
        raise ConfigError("不支持的操作。")
    if action == "打开配置目录":
        from prbot.config import prepare_env_directory

        path = prepare_env_directory()
        subprocess.run(["/usr/bin/open", "-R", str(path)], check=True)
        tell(
            f"请在这里填写配置：{path}\n"
            "填写 Telegram Token；仓库任务还需 GitHub Token、用户 ID 和仓库范围。\n"
            "Codex 登录请使用菜单导入。保存 .env 后重启服务。"
        )
    elif action == "选择工作路径":
        from prbot.task_config import select_work_root

        path = applescript(
            'POSIX path of (choose folder with prompt "选择统一存放 PRBot 任务工作区的文件夹")'
        )
        if path:
            selected = select_work_root(path)
            tell(
                f"工作路径已设为：{selected}\n"
                "已生成 PRBot-workspaces 文件夹及 .env.example、.env。\n"
                f"请填写：{selected}/PRBot-workspaces/.env\n"
                "重启服务后对新任务生效，已有任务不搬动。"
            )
    elif action == "配置任务":
        from prbot.execution_setup import configure_tasks

        values = []
        for prompt, hidden in [
            (
                "GitHub Token（代码/PR/Issue 写权限，Checks/Actions 读权限）",
                True,
            ),
            ("允许的 Telegram 用户 ID（逗号分隔）", False),
            ("允许的仓库 owner/repo（逗号分隔）", False),
        ]:
            label = json.dumps(prompt, ensure_ascii=False)
            value = applescript(
                f"text returned of (display dialog {label} "
                'default answer "" with title "PRBot"' + (" with hidden answer)" if hidden else ")")
            )
            if value is None:
                break
            values.append(value)
        if len(values) == 3:
            configure_tasks(*values)
            tell("任务配置已保存，已导入当前 Codex 登录。重启服务后发送 /FinishIssue。")
    elif action == "导入 Codex 登录":
        from prbot.execution_setup import import_codex_login

        import_codex_login()
        tell("Codex 登录已导入钥匙串；登录过期后请重新导入，并重启服务。")
    elif action == "配置 Token":
        value = applescript("""
text returned of (display dialog "请输入 BotFather 提供的 Token（存入 macOS 钥匙串）。" ¬
    default answer "" with hidden answer with title "PRBot")
""")
        if value is None:
            return
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


def show():
    from prbot.desktop import show as show_window

    show_window()

"""Isolated clones from a clean committed snapshot; never alter the user's checkout."""

import asyncio
import base64
import os
import re
from pathlib import Path

from prbot.github import DeliveryError
from prbot.paths import data_dir


def git_env(token=None):
    env = {k: v for k, v in os.environ.items() if k in {"HOME", "TMPDIR", "LANG", "SYSTEMROOT"}}
    env.update(
        PATH="/usr/bin:/bin",
        GIT_TERMINAL_PROMPT="0",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
    )
    if token:
        auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
            GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {auth}",
        )
    return env


async def git(cwd, *args, token=None):
    proc = await asyncio.create_subprocess_exec(
        "/usr/bin/git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "protocol.file.allow=always",
        *args,
        cwd=cwd,
        env=git_env(token),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        async with asyncio.timeout(120):
            out, _ = await proc.communicate()
        if proc.returncode:
            raise DeliveryError("Git 操作失败，请检查仓库、网络、Git 权限和任务工作区。")
        return out.decode().strip()
    finally:
        if proc.returncode is None:
            import signal

            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()


async def prepare(task, settings, token):
    root = (
        Path(settings.workspace_root) / "PRBot-workspaces"
        if settings.workspace_root
        else data_dir() / "workspaces"
    )
    if root.is_symlink():
        raise DeliveryError("工作路径下 PRBot-workspaces 不能是符号链接。")
    root.mkdir(mode=0o700, exist_ok=True)
    if root.stat().st_uid != os.getuid():
        raise DeliveryError("任务目录不属于当前用户，请重新选择工作路径。")
    root.chmod(0o700)
    path = root / task["id"]
    if path.exists():
        raise DeliveryError("任务工作区已存在，需人工核对；未覆盖已有文件。")
    repo = task["repo"]
    local = settings.local_repos.get(repo)
    url = f"https://github.com/{repo}.git"
    if local:
        local = str(Path(local).expanduser().resolve())
        origin = await git(local, "remote", "get-url", "origin")
        if origin.lower().removesuffix(".git") != url.lower().removesuffix(".git"):
            raise DeliveryError("配置的本地仓库 origin 与目标仓库不一致。")
        await git(root, "clone", "--no-hardlinks", "--no-checkout", local, str(path))
        await git(path, "remote", "set-url", "origin", url)
    else:
        await git(root, "clone", "--no-checkout", url, str(path), token=token)
    default = task["repository"]["default_branch"]
    await git(path, "fetch", "origin", default, token=token)
    sha = await git(path, "rev-parse", "FETCH_HEAD")
    await git(path, "checkout", "-b", task["branch"], sha)
    return path, sha


def safe_path(root, name):
    path = Path(name)
    if (
        path.is_absolute()
        or not path.parts
        or ".." in path.parts
        or any(p.startswith(".") and p not in {".github", ".gitignore"} for p in path.parts)
        or any(p.lower() in {"prbot.env", "credentials.json", "auth.json"} for p in path.parts)
    ):
        raise DeliveryError("交付包含不允许的路径或凭据文件。")
    target = root / path
    if not target.resolve().is_relative_to(root.resolve()) or target.is_symlink():
        raise DeliveryError("交付路径超出任务工作区。")
    return target


def apply_edits(root, proposal):
    edits = proposal["edits"]
    if not edits or len(edits) > 100:
        raise DeliveryError("没有可交付变更，或变更文件过多。")
    seen = set()
    for edit in edits:
        name = edit["path"]
        if name in seen:
            raise DeliveryError("交付路径重复。")
        seen.add(name)
        target = safe_path(root, name)
        if edit["content"] is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit["content"])


async def inspect_diff(path, task, secrets=()):
    await git(path, "add", "--all")
    await git(path, "diff", "--cached", "--check")
    names = (await git(path, "diff", "--cached", "--name-only")).splitlines()
    if not names:
        raise DeliveryError("没有实际代码变更。")
    for name in names:
        safe_path(path, name)
    diff = await git(path, "diff", "--cached", "--no-ext-diff", "--no-textconv")
    if len(diff) > 500000 or re.search(
        r"(?i)(-----BEGIN .*PRIVATE KEY|gh[pousr]_[A-Za-z0-9]{20}|"
        r"sk-[A-Za-z0-9_-]{20})",
        diff,
    ):
        raise DeliveryError("差异过大或可能包含凭据，已停止交付。")
    if any(secret and secret in diff for secret in secrets):
        raise DeliveryError("差异包含执行凭据，已停止交付。")
    if not any(name.lower().endswith(".md") for name in names):
        raise DeliveryError("交付缺少文档更新。")
    rules = "\n".join(item["instructions"] for item in task.get("parent_rules", []))
    if "4象限" in rules or "四象限" in rules:
        log = path / "tasklist.md"
        body = log.read_text() if log.is_file() else ""
        headings = ("## 想做：", "## 做完：", "## 没做：", "## 在做：")
        records = [line.strip() for line in body.splitlines() if line.strip().startswith("- ")]
        if not all(heading in body for heading in headings) or any(
            not re.search(r"\b[0-9]{9,11}\b", line) for line in records
        ):
            raise DeliveryError("tasklist.md 不符合仓库四象限和每条记录带 Unix 时间戳的约定。")
    requirement = task["requirement"]
    if "test.md" in requirement and "test done" in requirement:
        target = path / "test.md"
        if not target.is_file() or target.read_text() not in {"test done", "test done\n"}:
            raise DeliveryError("test.md 内容未达到要求。")
    return diff, names

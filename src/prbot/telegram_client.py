import asyncio
import logging
import random
from datetime import timedelta

from telegram import Bot
from telegram.error import (
    BadRequest,
    Conflict,
    Forbidden,
    InvalidToken,
    NetworkError,
    RetryAfter,
    TelegramError,
)

from prbot.command_router import CommandRouter
from prbot.tasks import Conversations, TaskStore

log = logging.getLogger(__name__)


class ServiceHalted(Exception):
    """An actionable error: exit successfully so launchd does not restart it."""


def backoff(attempt):
    return min(60.0, 2 ** min(attempt, 6)) * random.uniform(0.75, 1.0)


async def pause(stop, seconds):
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def poll_until_stopped(bot, stop, **kwargs):
    # Interrupt a long poll immediately on SIGTERM instead of waiting past launchd's grace.
    poll = asyncio.create_task(bot.get_updates(**kwargs))
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait({poll, stopping}, return_when=asyncio.FIRST_COMPLETED)
        return await poll if poll in done else ()
    finally:
        for task in (poll, stopping):
            if not task.done():
                task.cancel()
        await asyncio.gather(poll, stopping, return_exceptions=True)


def retry_seconds(error):
    value = error.retry_after
    return value.total_seconds() if isinstance(value, timedelta) else value


class Receiver:
    def __init__(self, bot, state, router):
        self.bot, self.state, self.router = bot, state, router

    async def handle(self, update):
        uid = update.update_id
        previous = self.state.record(uid)
        if uid < self.state.offset:
            return
        if previous and previous[0] in {"delivered", "ignored", "failed_permanent"}:
            self.state.mark(uid, previous[0], advance=True)
            return
        if previous and previous[1] >= 5:
            raise ServiceHalted("回复已尝试 5 次，请检查日志和进度库；确认后按手册处理。")
        self.state.mark(uid, "processing")
        try:
            store = TaskStore(self.state)
            reply = store.reply(uid)
            if reply is None:
                reply = await self.router.dispatch_async(update)
                if reply is not None:
                    with self.state.db:
                        store.save_reply(uid, reply)
        except Exception as error:
            self.state.mark(uid, "failed", type(error).__name__, attempt=True)
            log.error("命令处理异常 type=%s", type(error).__name__)
            # Do not acknowledge a failed handler; recoverable retry, bounded by attempts.
            raise NetworkError("command handler failed") from None
        if reply is None:
            self.state.mark(uid, "ignored", advance=True)
            return
        self.state.mark(uid, "response_pending", attempt=True)
        log.info("命令已接收，准备回复")
        try:
            await self.bot.send_message(
                chat_id=update.message.chat.id,
                text=reply,
                message_thread_id=update.message.message_thread_id,
            )
        except (Forbidden, BadRequest) as error:
            # A definite API rejection, never call it delivered. Retain diagnostics.
            self.state.mark(uid, "failed_permanent", type(error).__name__, advance=True)
            log.error("回复被拒绝 type=%s；请检查 Bot 会话权限", type(error).__name__)
            return
        except TelegramError as error:
            self.state.mark(uid, "response_pending", type(error).__name__)
            log.warning("回复未确认 type=%s；重试可能造成重复回复", type(error).__name__)
            raise
        self.state.mark(uid, "delivered", advance=True)
        log.info("命令回复已确认，处理进度已保存")


async def run(config, state, stop, report):
    bot = Bot(config.token)
    failures = 0
    initialized = False
    background = []
    from prbot.task_config import TaskConfig

    settings = TaskConfig.load()
    store = TaskStore(state)
    conversations = Conversations(store, settings) if settings.enabled else None
    try:
        while not stop.is_set():
            try:
                if not initialized:
                    await bot.initialize()  # Calls getMe: validate credentials before polling.
                    if (await bot.get_webhook_info()).url:
                        raise ServiceHalted("Bot 已配置 Webhook，请先停用原服务并删除 Webhook。")
                    initialized = True
                    receiver = Receiver(bot, state, CommandRouter(bot.username, conversations))
                    if settings.enabled:
                        from prbot.codex_adapter import CodexAdapter
                        from prbot.execution_setup import execution_credentials
                        from prbot.executor import Executor, send_outbox
                        from prbot.github import GitHub

                        token, auth = execution_credentials()
                        github = GitHub(token)
                        conversations.github = github
                        codex = CodexAdapter(auth)
                        from prbot.github import DeliveryError

                        try:
                            await codex.check_auth()
                        except (DeliveryError, TimeoutError):
                            await github.close()
                            raise ServiceHalted(
                                "Codex 启动检查失败，请重新导入登录并检查运行时。"
                            ) from None
                        executor = Executor(store, settings, github, codex, token)
                        background = [
                            asyncio.create_task(executor.run(stop)),
                            asyncio.create_task(send_outbox(store, bot, stop)),
                        ]
                    log.info("Token API 校验成功，开始长轮询")
                for worker in background:
                    if worker.done() and worker.exception():
                        raise ServiceHalted("任务后台异常退出，请查看任务状态并重启服务。")
                report("running", "Telegram 已连接，正在监听")
                updates = await poll_until_stopped(
                    bot,
                    stop,
                    offset=state.offset,
                    timeout=config.poll_timeout,
                    read_timeout=config.poll_timeout + 10,
                    allowed_updates=["message"],
                )
                for update in updates:
                    if stop.is_set():
                        break
                    await receiver.handle(update)
                if failures:
                    log.info("Telegram 连接已恢复")
                failures = 0
            except (InvalidToken, Conflict, Forbidden) as error:
                advice = {
                    InvalidToken: "Token 无效，请重新配置。",
                    Conflict: "轮询冲突，请停止使用同一 Token 的其他程序后再启动。",
                    Forbidden: "API 权限拒绝，请检查 Bot 权限和 Token。",
                }
                raise ServiceHalted(advice[type(error)]) from None
            except RetryAfter as error:
                report("reconnecting", "Telegram 要求稍后重试")
                await pause(stop, retry_seconds(error) + random.uniform(0.1, 1))
            except NetworkError as error:
                failures += 1
                delay = backoff(failures)
                report("reconnecting", "网络或回复异常，正在退避重连")
                log.warning("通信异常 type=%s，%.1f 秒后重试", type(error).__name__, delay)
                await pause(stop, delay)
            except TelegramError as error:
                raise ServiceHalted(
                    f"API 错误 {type(error).__name__}，请检查配置和日志。"
                ) from None
    finally:
        stop.set()
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        if background:
            await github.close()
        await bot.shutdown()

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
from prbot.tasks import TaskStore

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
            raise ServiceHalted(
                "Reply attempted 5 times. Review logs and the progress database before retrying."
            )
        self.state.mark(uid, "processing")
        try:
            archive = getattr(self.router.conversations, "archive_update", None)
            if archive:
                await archive(update)
            store = TaskStore(self.state)
            reply = store.reply(uid)
            if reply is None:
                reply = await self.router.dispatch_async(update)
                if reply is not None:
                    with self.state.db:
                        store.save_reply(uid, reply)
        except Exception as error:
            self.state.mark(uid, "failed", type(error).__name__, attempt=True)
            log.error("Command handler failed type=%s", type(error).__name__)
            # Do not acknowledge a failed handler; recoverable retry, bounded by attempts.
            raise NetworkError("command handler failed") from None
        if reply is None:
            self.state.mark(uid, "ignored", advance=True)
            return
        if archive:
            try:
                await archive(update, reply)
            except Exception:
                raise NetworkError("Message not saved. Retry.") from None
        self.state.mark(uid, "response_pending", attempt=True)
        log.info("Command received; preparing reply")
        try:
            await self.bot.send_message(
                chat_id=update.message.chat.id,
                text=reply,
                message_thread_id=update.message.message_thread_id,
            )
        except (Forbidden, BadRequest) as error:
            # A definite API rejection, never call it delivered. Retain diagnostics.
            self.state.mark(uid, "failed_permanent", type(error).__name__, advance=True)
            log.error("Reply rejected type=%s; check bot chat permissions", type(error).__name__)
            return
        except TelegramError as error:
            self.state.mark(uid, "response_pending", type(error).__name__)
            log.warning(
                "Reply unconfirmed type=%s; retries may duplicate delivery", type(error).__name__
            )
            raise
        self.state.mark(uid, "delivered", advance=True)
        log.info("Reply confirmed; progress saved")


async def run(config, state, stop, report):
    bot = Bot(config.token)
    failures = 0
    initialized = False
    background = []
    from prbot.conversation_service import ConversationService
    from prbot.telegram_conversations import TelegramConversations, send_results

    application = ConversationService()
    conversations = TelegramConversations(application)
    try:
        while not stop.is_set():
            try:
                if not initialized:
                    await bot.initialize()  # Calls getMe: validate credentials before polling.
                    if (await bot.get_webhook_info()).url:
                        raise ServiceHalted(
                            "Bot has a webhook. Stop the previous service and remove the "
                            "webhook first."
                        )
                    initialized = True
                    receiver = Receiver(bot, state, CommandRouter(bot.username, conversations))
                    background = [
                        asyncio.create_task(application.run(stop)),
                        asyncio.create_task(send_results(application, bot, stop)),
                    ]
                    log.info("Telegram verified; starting long polling")
                for worker in background:
                    if worker.done() and worker.exception():
                        raise ServiceHalted(
                            "Task worker stopped unexpectedly. Review tasks and restart the "
                            "service."
                        )
                report("running", "Telegram connected and listening")
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
                    log.info("Telegram connection restored")
                failures = 0
            except (InvalidToken, Conflict, Forbidden) as error:
                advice = {
                    InvalidToken: "Invalid Telegram token. Update Infisical and restart.",
                    Conflict: "Polling conflict. Stop other programs using the same Telegram "
                    "token.",
                    Forbidden: "Telegram access denied. Check bot permissions and token in "
                    "Infisical.",
                }
                raise ServiceHalted(advice[type(error)]) from None
            except RetryAfter as error:
                report("reconnecting", "Telegram requested a retry delay")
                await pause(stop, retry_seconds(error) + random.uniform(0.1, 1))
            except NetworkError as error:
                failures += 1
                delay = backoff(failures)
                report("reconnecting", "Connection interrupted; reconnecting with backoff")
                log.warning(
                    "Connection error type=%s; retry in %.1f seconds", type(error).__name__, delay
                )
                await pause(stop, delay)
            except TelegramError as error:
                raise ServiceHalted(
                    f"Telegram API error {type(error).__name__}. Check Infisical and logs."
                ) from None
    finally:
        stop.set()
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await bot.shutdown()

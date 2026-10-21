import re

from prbot.commands.id import identity


class CommandRouter:
    def __init__(self, username, conversations=None):
        self.username = username.lower()
        self.conversations = conversations
        self.handlers = {}
        self.register("id", identity)

    def register(self, name, handler):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in self.handlers:
            raise ValueError("命令名称无效或已注册")
        self.handlers[name] = handler

    def dispatch(self, update):
        message = update.message
        if message is None or message.chat.type not in {"private", "group", "supergroup"}:
            return None
        text = message.text or ""
        match = re.match(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?=\s|$)", text)
        if not match:
            if self.conversations:
                return self.conversations.handle(update)
            return None
        name, target = match.groups()
        if target and target.lower() != self.username:
            return None
        if name.lower() in {"finishissue", "cancel"}:
            if self.conversations:
                return self.conversations.handle(update, name.lower())
            return "仓库任务未启用，请先配置凭据和允许范围。"
        handler = self.handlers.get(name.lower())
        return handler(message) if handler else "当前仅支持 /id，发送它可查看身份信息。"

    async def dispatch_async(self, update):
        message = update.message
        if (
            not self.conversations
            or message is None
            or message.chat.type not in {"private", "group", "supergroup"}
        ):
            return self.dispatch(update)
        text = message.text or ""
        match = re.match(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?=\s|$)", text)
        if match:
            name, target = match.groups()
            if target and target.lower() != self.username:
                return None
            if name.lower() in {"finishissue", "cancel"}:
                return await self.conversations.handle_async(update, name.lower())
            return self.dispatch(update)
        return await self.conversations.handle_async(update)

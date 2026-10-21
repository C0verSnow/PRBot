import re

from prbot.commands.id import identity


class CommandRouter:
    def __init__(self, username):
        self.username = username.lower()
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
            return None
        name, target = match.groups()
        if target and target.lower() != self.username:
            return None
        handler = self.handlers.get(name.lower())
        return handler(message) if handler else "当前仅支持 /id，发送它可查看身份信息。"

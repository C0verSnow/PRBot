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
            raise ValueError("Command name is invalid or already registered")
        self.handlers[name] = handler

    def dispatch(self, update):
        message = update.message
        if message is None or message.chat.type not in {"private", "group", "supergroup"}:
            return None
        text = message.text or ""
        match = re.match(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?=\s|$)", text)
        if not match:
            if self.conversations:
                raise NotImplementedError("Use dispatch_async for conversation messages.")
            return None
        name, target = match.groups()
        if target and target.lower() != self.username:
            return None
        if name.lower() in {"finishissue", "cancel", "answer"}:
            if self.conversations:
                raise NotImplementedError("Use dispatch_async for conversation commands.")
            return "Repository tasks are unavailable. Check Infisical and Codex sign-in."
        handler = self.handlers.get(name.lower())
        return handler(message) if handler else "Supported commands: /id, /FinishIssue and /cancel."

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
            if name.lower() in {"finishissue", "cancel", "answer"}:
                return await self.conversations.handle_async(update, name.lower())
            return self.dispatch(update)
        return await self.conversations.handle_async(update)

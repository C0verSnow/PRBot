def identity(message):
    user = message.from_user
    if message.sender_chat is not None or user is None or user.is_bot:
        return "无法确定个人 User ID；请使用个人账号在私聊或群组发送 /id。"
    return (
        "PRBot · Identity\n\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {message.chat.id}\n"
        f"Chat Type: {message.chat.type}"
    )

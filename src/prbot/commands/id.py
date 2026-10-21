def identity(message):
    user = message.from_user
    if message.sender_chat is not None or user is None or user.is_bot:
        return "Cannot identify a personal User ID. Send /id from a personal account."
    return (
        "PRBot · Identity\n\n"
        f"User ID: {user.id}\n"
        f"Chat ID: {message.chat.id}\n"
        f"Chat Type: {message.chat.type}"
    )

from prbot.commands.id import identity


def test_private(update):
    assert identity(update().message) == (
        "PRBot · Identity\n\nUser ID: 123\nChat ID: 123\nChat Type: private"
    )


def test_group_uses_sender_not_chat(update):
    reply = identity(update(user_id=123, chat_id=-999, chat_type="group").message)
    assert "User ID: 123" in reply
    assert "Chat ID: -999" in reply


def test_missing_sender(update):
    assert "Cannot identify" in identity(update(user_id=None).message)


def test_anonymous_admin(update):
    message = update(sender_chat={"id": -999, "type": "supergroup"}).message
    assert "Cannot identify" in identity(message)
    assert "User ID: 123" not in identity(message)

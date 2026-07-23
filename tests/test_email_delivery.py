import socket
from email import policy
from email.parser import BytesParser

from aiosmtpd.controller import Controller

from na_sso.config import EmailChannel
from na_sso.email_delivery import send_email


class CapturingHandler:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(
            BytesParser(policy=policy.default).parsebytes(envelope.content)
        )
        return "250 Message accepted for delivery"


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def test_send_email_delivers_plain_text_message():
    handler = CapturingHandler()
    controller = Controller(
        handler,
        hostname="127.0.0.1",
        port=_unused_loopback_port(),
    )
    controller.start()
    try:
        channel = EmailChannel(
            enabled=True,
            host="127.0.0.1",
            port=controller.port,
            from_address="na-sso@example.test",
            tls_mode="none",
            events=["password.expired"],
        )

        await send_email(
            channel,
            to="operator@example.test",
            subject="Password expired",
            body="The managed password has expired.",
        )
    finally:
        controller.stop()

    assert len(handler.messages) == 1
    message = handler.messages[0]
    assert str(message["From"]) == "na-sso@example.test"
    assert str(message["To"]) == "operator@example.test"
    assert str(message["Subject"]) == "Password expired"
    assert message.get_body().get_content().strip() == "The managed password has expired."

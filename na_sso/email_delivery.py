from email.message import EmailMessage

import aiosmtplib

from na_sso.config import EmailChannel


async def send_email(
    channel: EmailChannel,
    *,
    to: str,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = channel.from_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    auth: dict[str, str] = {}
    if channel.username is not None:
        auth["username"] = channel.username
    if channel.password is not None:
        auth["password"] = channel.password.get_secret_value()

    await aiosmtplib.send(
        message,
        hostname=channel.host,
        port=channel.port,
        use_tls=channel.tls_mode == "tls",
        start_tls=channel.tls_mode == "starttls",
        **auth,
    )

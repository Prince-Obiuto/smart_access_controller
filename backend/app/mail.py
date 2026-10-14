"""Bounded SMTP delivery. Never log mail bodies, credentials or reset links."""

import smtplib
import ssl
from email.message import EmailMessage


def available(settings):
    return bool(settings.smtp_host and settings.smtp_from)


def send(settings, recipient, subject, body):
    if not available(settings):
        raise OSError("Recovery delivery unavailable")
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    transport = smtplib.SMTP_SSL if settings.smtp_security == "TLS" else smtplib.SMTP
    kwargs = (
        {"context": ssl.create_default_context()}
        if settings.smtp_security == "TLS"
        else {}
    )
    with transport(
        settings.smtp_host, settings.smtp_port, timeout=8, **kwargs
    ) as client:
        if settings.smtp_security == "STARTTLS":
            client.starttls(context=ssl.create_default_context())
        if settings.smtp_username.get_secret_value():
            client.login(
                settings.smtp_username.get_secret_value(),
                settings.smtp_password.get_secret_value(),
            )
        if client.send_message(message):
            raise OSError("Recovery recipient refused")

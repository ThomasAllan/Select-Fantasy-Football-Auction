import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from select_football.common.logging import get_logger
from select_football.config import Settings

log = get_logger(__name__)


def send_report(
    html_body: str,
    recipients: list[str],
    subject: str,
    settings: Settings,
    text_body: str | None = None,
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    # Everyone goes in a visible "To" — this is a friends' league and a shared
    # thread is wanted. (Addresses are therefore visible to all recipients.)
    msg["To"] = ", ".join(recipients)

    # A text/plain part must come first; clients render the last part they
    # understand. An HTML-only message is a common spam-filter trigger.
    if text_body:
        msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    log.info("sending_email", recipients=recipients, subject=subject)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.email_from, recipients, msg.as_string())

    log.info("email_sent", count=len(recipients))

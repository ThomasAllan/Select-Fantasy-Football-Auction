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
) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = settings.email_from
    msg["To"] = ", ".join(recipients)

    msg.attach(MIMEText(html_body, "html"))

    log.info("sending_email", recipients=recipients, subject=subject)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.email_from, recipients, msg.as_string())

    log.info("email_sent", count=len(recipients))

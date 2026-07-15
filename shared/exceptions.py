import logging
import traceback
import sys
from pipeline.shared.config_loader import ConfigLoader
from pipeline.shared.secret_reader import SecretReader
import smtplib
import socket
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


ip_mapping = {
    "10.0.133.119": "Workstation 1",
    "10.0.152.71": "Workstation 6",
    # weitere IPs hier ergänzen
}
class QualityGateFailed(Exception):
    """Raised when a quality check fails."""
    pass

def send_mail(type, subject, message):

    logger = logging.getLogger(__name__)
    c = ConfigLoader()

    sec_reader = SecretReader()
    secret = sec_reader.get_secret(secret_name="/reporting/aws_smtp", session=c.conf['session'])

    SMTP_HOST = secret['host']
    SMTP_PORT = int(secret['port'])  # oder 465 für SSL

    smtp_username = secret['username']  # Deine SES SMTP Credentials
    smtp_password = secret['password']  # Dein SES SMTP Passwort

    sender = "targeting@exaris-solutions.de"
    recipient = "targeting@exaris-solutions.de"
    body = message  # Hier dein Stacktrace

    # Zusätzliche Infos
    hostname = socket.gethostname()

    # IP-Adresse der Maschine ermitteln
    try:
        ip_address = socket.gethostbyname(hostname)
        try:
            friendly_name = ip_mapping.get(ip_address, ip_address)
        except:
            friendly_name = "friendly name not available"
    except Exception:
        ip_address = "unknown ip address"
        friendly_name = "unknown friendly name"

    current_time = datetime.now().strftime("%H:%M:%S")
    current_date = datetime.now().strftime("%d.%m.%Y")

    # HTML-Mail bauen
    html_body = f"""
    <html>
      <body>
        <h2>{subject}</h2>
        <table style="border-collapse: collapse; width: 20%; margin-bottom: 50px;">
          <tr>
            <th style="width: 40%; text-align: left; border: 1px solid #ccc; padding: 8px; background-color: #f2f2f2;">Feld</th>
            <th style="width: 60%; text-align: left; border: 1px solid #ccc; padding: 8px; background-color: #f2f2f2;">Wert</th>
            </tr>
          <tr>
            <td style="border: 1px solid #ccc; padding: 8px;">Machine</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{friendly_name}</td>
          </tr>
          <tr>
            <td style="border: 1px solid #ccc; padding: 8px;">IP address</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{ip_address}</td>
          </tr>
          <tr>
            <td style="border: 1px solid #ccc; padding: 8px;">Date</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{current_date}</td>
          </tr>
          <tr>
            <td style="border: 1px solid #ccc; padding: 8px;">Time</td>
            <td style="border: 1px solid #ccc; padding: 8px;">{current_time}</td>
          </tr>
        </table>

        <h3>Details:</h3>
        <pre style="background-color:#f4f4f4; padding:10px; border:1px solid #ccc; white-space: pre-wrap;">{body}</pre>
      </body>
    </html>
    """

    # MIME Multipart Nachricht erstellen
    msg = MIMEMultipart("alternative")
    if type=="qg":
        msg["Subject"] = f"{c.conf['project_config'].upper()} | Quality Gate not passed"
    elif type=="error":
        msg["Subject"] = f"{c.conf['project_config'].upper()} | Exception occured"
    elif type=="finished":
        msg["Subject"] = f"{c.conf['project_config'].upper()} | Targeting finished"

    msg["From"] = sender
    msg["To"] = recipient

    # Plaintext-Fallback (optional)
    plain_text = f"{subject}\n\nDate: {current_date}\nTime: {current_time}\nHostname: {hostname}\nIP address: {ip_address}\n\n{body}"

    # Parts hinzufügen
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Mail senden
    if c.conf['is_ecs']:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(sender, [recipient], msg.as_string())
        logger.info(" -> E-Mail send")

def handle_quality_gate(e, logger=None, show_traceback=False, reraise=True):

    if logger is None:
        logger = logging.getLogger(__name__)

    if e is None:
        logger.info(" ✅ Quality gate passed")
        logger.info("=============================================================")
    else:

        logger.warning(f"⚠️ Quality Gate failed: {e}")
        logger.info("=============================================================")

        if show_traceback:
            logger.error(traceback.format_exc())

        if reraise:
            #raise e
            subject = e.args[0]
            message = traceback.format_exc()
            #send_mail(type="qg", subject=subject, message=message)
            sys.exit(1)

def handle_exception(e, logger=None, show_traceback=True, reraise=True):
    if logger is None:
        logger = logging.getLogger(__name__)

    logger.error(f" ❌ Error: {e}")

    if show_traceback:
        logger.error(traceback.format_exc())

    if reraise:
        subject = e.args[0]
        message = traceback.format_exc()
        #send_mail(type="error", subject=subject, message=message)
        sys.exit(1)
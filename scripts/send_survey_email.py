"""
One-time script to send a feedback survey email to all registered Meeloop users.

Usage:
    cd backend && source env/bin/activate
    python send_survey_email.py

    # Dry run (prints recipients without sending):
    python send_survey_email.py --dry-run
"""

import asyncio
import sys
from sqlalchemy import create_engine, text
from app.config import settings
from fastapi_mail import FastMail, MessageSchema, ConnectionConfig, MessageType

DRY_RUN = "--dry-run" in sys.argv

# ── Email config ──────────────────────────────────────────────────────────────

conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=settings.USE_CREDENTIALS,
    VALIDATE_CERTS=True,
)

fastmail = FastMail(conf)

# ── Email template ────────────────────────────────────────────────────────────

def build_email_html(name: str) -> str:
    first_name = name.split()[0] if name else "there"
    return f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>How did you find Meeloop?</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:'Helvetica Neue',Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f5;padding:32px 0;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0"
               style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

          <!-- Header -->
          <tr>
            <td style="background:linear-gradient(135deg,#6366f1 0%,#8b5cf6 100%);padding:32px 40px;text-align:center;">
              <h1 style="margin:0;color:#ffffff;font-size:26px;font-weight:700;letter-spacing:-0.5px;">Meeloop</h1>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding:40px;">
              <p style="margin:0 0 20px;font-size:16px;color:#374151;line-height:1.7;">
                Hey <strong>{first_name}</strong>,
              </p>
              <p style="margin:0 0 20px;font-size:16px;color:#374151;line-height:1.7;">
                We're curious — <strong>how did you first hear about Meeloop?</strong>
              </p>
              <p style="margin:0 0 20px;font-size:16px;color:#374151;line-height:1.7;">
                Was it a friend, social media, the Play Store, something else? Even one line helps us a lot.
              </p>
              <p style="margin:0 0 32px;font-size:16px;color:#374151;line-height:1.7;">
                Just hit <strong>Reply</strong> and let us know. We read every response personally.
              </p>
              <p style="margin:0;font-size:16px;color:#374151;">
                — The Meeloop Team
              </p>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb;padding:18px 40px;border-top:1px solid #e5e7eb;text-align:center;">
              <p style="margin:0;font-size:12px;color:#9ca3af;">
                You're receiving this because you have a Meeloop account.<br/>
                If you'd rather not hear from us, just reply "unsubscribe".
              </p>
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


# ── Main logic ────────────────────────────────────────────────────────────────

async def send_survey():
    engine = create_engine(settings.DATABASE_URL, echo=False)
    with engine.connect() as conn:
        result = conn.execute(text(
            'SELECT name, email FROM "user" WHERE is_active = true AND email IS NOT NULL AND email != \'\''
        ))
        users = result.fetchall()

    print(f"Found {len(users)} active users with email addresses.")

    if DRY_RUN:
        print("\n[DRY RUN] Would send to:")
        for u in users:
            print(f"  {u.name} <{u.email}>")
        return

    sent = 0
    failed = 0

    for user in users:
        try:
            message = MessageSchema(
                subject="How did you find Meeloop? 👋",
                recipients=[user.email],
                body=build_email_html(user.name),
                subtype=MessageType.html,
            )
            await fastmail.send_message(message)
            print(f"  ✓  {user.email}")
            sent += 1
            # Small delay to avoid overwhelming the SMTP server
            await asyncio.sleep(1)
        except Exception as e:
            print(f"  ✗  {user.email}  ({e})")
            failed += 1

    print(f"\nDone. Sent: {sent}  Failed: {failed}")


if __name__ == "__main__":
    if not settings.MAIL_USERNAME or not settings.MAIL_PASSWORD:
        print("ERROR: Email is not configured. Set MAIL_USERNAME and MAIL_PASSWORD in .env")
        sys.exit(1)

    asyncio.run(send_survey())

import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from app.config import get_settings

logger = logging.getLogger(__name__)


def build_invite_html(team_name: str, role: str, invited_by_name: str,
                      invited_by_email: str, requests_url: str) -> str:
    role_colors = {"owner": "#6366f1", "admin": "#f59e0b", "member": "#10b981"}
    color = role_colors.get(role, "#10b981")
    inviter = f"{invited_by_name} ({invited_by_email})" if invited_by_name else invited_by_email
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e4e4e7">
        <tr><td style="background:#18181b;padding:24px 32px">
          <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:-0.5px">CostAdvisor</span>
        </td></tr>
        <tr><td style="padding:32px">
          <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#18181b">You've been invited</p>
          <p style="margin:0 0 24px;color:#71717a;font-size:14px">
            <strong style="color:#18181b">{inviter}</strong>
            has invited you to join <strong style="color:#18181b">{team_name}</strong> on CostAdvisor.
          </p>
          <table cellpadding="0" cellspacing="0" style="margin-bottom:24px">
            <tr>
              <td style="padding-right:12px;color:#71717a;font-size:13px">Team</td>
              <td style="font-size:13px;font-weight:600;color:#18181b">{team_name}</td>
            </tr>
            <tr><td style="padding:4px 0"></td></tr>
            <tr>
              <td style="padding-right:12px;color:#71717a;font-size:13px">Your role</td>
              <td>
                <span style="display:inline-block;padding:2px 10px;border-radius:4px;font-size:12px;
                             font-weight:600;background:{color}22;color:{color}">{role.capitalize()}</span>
              </td>
            </tr>
          </table>
          <p style="margin:0 0 24px;color:#52525b;font-size:13px;line-height:1.6">
            Sign in to CostAdvisor to accept or decline this invitation.
            The invite expires in 7 days.
          </p>
          <a href="{requests_url}"
             style="display:inline-block;background:#18181b;color:#fff;
                    text-decoration:none;padding:12px 28px;border-radius:6px;
                    font-size:14px;font-weight:600">
            View Invitation
          </a>
        </td></tr>
        <tr><td style="padding:20px 32px;border-top:1px solid #f4f4f5;background:#fafafa">
          <p style="margin:0;font-size:11px;color:#a1a1aa">
            If you weren't expecting this, you can safely ignore it.<br>
            &copy; CostAdvisor &middot;
            <a href="https://costadvisor.org" style="color:#a1a1aa">costadvisor.org</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _send(to_email: str, subject: str, html: str) -> bool:
    """Low-level SMTP send. Returns True on success, False if not configured or failed."""
    settings = get_settings()
    if not settings.smtp_host or not settings.smtp_user:
        logger.warning("SMTP not configured — email not sent to %s", to_email)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("CostAdvisor", settings.email_from))
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        if settings.smtp_tls:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(settings.email_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
                smtp.login(settings.smtp_user, settings.smtp_password)
                smtp.sendmail(settings.email_from, [to_email], msg.as_string())
        return True
    except Exception as e:
        logger.warning("Failed to send email to %s: %s", to_email, e)
        return False


def build_welcome_html(display_name: str, app_url: str, heading: str, body_lines: list[str], cta_label: str) -> str:
    body_html = "".join(
        f'<p style="margin:0 0 12px;color:#52525b;font-size:13px;line-height:1.6">{line}</p>'
        for line in body_lines
    )
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr><td align="center" style="padding:40px 20px">
      <table width="560" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:8px;overflow:hidden;border:1px solid #e4e4e7">
        <tr><td style="background:#18181b;padding:24px 32px">
          <span style="color:#fff;font-size:18px;font-weight:700;letter-spacing:-0.5px">CostAdvisor</span>
        </td></tr>
        <tr><td style="padding:32px">
          <p style="margin:0 0 8px;font-size:22px;font-weight:700;color:#18181b">{heading}</p>
          {body_html}
          <a href="{app_url}"
             style="display:inline-block;background:#18181b;color:#fff;
                    text-decoration:none;padding:12px 28px;border-radius:6px;
                    font-size:14px;font-weight:600;margin-top:8px">
            {cta_label}
          </a>
        </td></tr>
        <tr><td style="padding:20px 32px;border-top:1px solid #f4f4f5;background:#fafafa">
          <p style="margin:0;font-size:11px;color:#a1a1aa">
            &copy; CostAdvisor &middot;
            <a href="https://costadvisor.org" style="color:#a1a1aa">costadvisor.org</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def send_access_granted_email(to_email: str, app_url: str) -> bool:
    """Notify a user that their platform access request was approved."""
    html = build_welcome_html(
        display_name="",
        app_url=app_url,
        heading="You've been granted access to CostAdvisor",
        body_lines=[
            "Your access request has been approved.",
            "Sign in with your Google account to get started. "
            "Once signed in, a team administrator will add you to your team — "
            "or a team owner can invite you directly from their Team page.",
            "If you have any questions, reply to this email.",
        ],
        cta_label="Sign in to CostAdvisor →",
    )
    return _send(to_email, "You've been granted access to CostAdvisor", html)


def send_welcome_email(to_email: str, display_name: str, app_url: str) -> bool:
    """Welcome email sent when a new user account is created (via team-invite bypass)."""
    name = display_name or to_email.split("@")[0]
    html = build_welcome_html(
        display_name=name,
        app_url=app_url,
        heading=f"Welcome to CostAdvisor, {name}!",
        body_lines=[
            "Your account is ready.",
            "You've been invited to join a team — open the <strong>Requests</strong> tab "
            "inside the Team page to accept your invitation.",
            "From there you can start building should-cost models and running gap analysis "
            "against your supplier prices.",
        ],
        cta_label="Go to CostAdvisor →",
    )
    return _send(to_email, "Welcome to CostAdvisor", html)


def send_demo_request_received_email(to_email: str, name: str) -> bool:
    """Acknowledge receipt of a demo request — sent immediately on submission."""
    display = name or to_email.split("@")[0]
    settings = get_settings()
    html = build_welcome_html(
        display_name=display,
        app_url=settings.app_url,
        heading=f"We received your demo request, {display}!",
        body_lines=[
            "Thanks for your interest in CostAdvisor.",
            "We'll review your request and confirm the date and time shortly.",
            "You'll receive another email once your demo is confirmed, "
            "including a Google Meet link for the call.",
            "If you have any questions in the meantime, "
            "feel free to reply to this email.",
        ],
        cta_label="Learn more →",
    )
    return _send(to_email, "Demo request received — CostAdvisor", html)


def send_demo_confirmation_email(
    to_email: str,
    name: str,
    date_str: str,
    time_str: str,
    meet_link: str,
    host_name: str,
) -> bool:
    """Confirm an accepted demo — includes the Google Meet link."""
    display = name or to_email.split("@")[0]
    settings = get_settings()
    html = build_welcome_html(
        display_name=display,
        app_url=meet_link or settings.app_url,
        heading=f"Your demo is confirmed, {display}!",
        body_lines=[
            f"<strong>Date:</strong> {date_str}",
            f"<strong>Time:</strong> {time_str} UTC",
            f"<strong>Host:</strong> {host_name}",
            "Join the call using the Google Meet link below. "
            "You'll also receive a calendar invite shortly.",
            "If you need to reschedule, please reply to this email.",
        ],
        cta_label="Join Google Meet →",
    )
    return _send(to_email, "Your CostAdvisor demo is confirmed", html)


def send_invite_email(
    to_email: str,
    team_name: str,
    role: str,
    invited_by_name: str,
    invited_by_email: str,
) -> bool:
    """Send a team invite email via SMTP. Returns True on success, False if not configured or failed."""
    settings = get_settings()
    requests_url = f"{settings.app_url}/team?tab=requests"
    html = build_invite_html(team_name, role, invited_by_name, invited_by_email, requests_url)
    subject = f"{invited_by_name or invited_by_email} invited you to join {team_name} on CostAdvisor"
    return _send(to_email, subject, html)


def send_mention_email(to_email: str, mentioner_name: str, product_name: str,
                       note_snippet: str, link: str) -> bool:
    """Scrum 25 — notify a teammate they were @mentioned in a note on a cost model."""
    who = mentioner_name or "A teammate"
    html = build_welcome_html(
        display_name=to_email.split("@")[0],
        app_url=link,
        heading=f"{who} mentioned you",
        body_lines=[
            f"You were mentioned in a note on <strong>{product_name}</strong>:",
            f'<em style="color:#3f3f46">“{note_snippet}”</em>',
        ],
        cta_label="Open in CostAdvisor →",
    )
    return _send(to_email, f"{who} mentioned you on {product_name}", html)


def send_alert_email(to_email: str, message: str, link: str) -> bool:
    """Scrum 24 — deliver a triggered alert (index move / gap / buy window) by email."""
    html = build_welcome_html(
        display_name=to_email.split("@")[0],
        app_url=link,
        heading="CostAdvisor alert",
        body_lines=[message, "Open CostAdvisor to review and act."],
        cta_label="Open CostAdvisor →",
    )
    return _send(to_email, "CostAdvisor alert", html)

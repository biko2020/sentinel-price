# =============================================================================
#  SentinelPrice · Alert Manager
# =============================================================================
#  Detects price changes and dispatches notifications via Email and/or Slack.
#
#  How it works:
#    1. AlertPipeline (pipeline stage 500) calls AlertManager after every
#       PriceSnapshotItem is persisted.
#    2. AlertManager queries the DB for the previous price of the same SKU.
#    3. If a significant price change is detected, it builds an AlertEvent
#       and dispatches it to all enabled channels (Email, Slack).
#
#  Configuration (via .env):
#    ALERT_EMAIL_ENABLED, ALERT_SLACK_ENABLED
#    ALERT_PRICE_DROP_THRESHOLD      — minimum % drop to trigger alert (default 5)
#    ALERT_PRICE_INCREASE_THRESHOLD  — minimum % increase to trigger (default 10)
#
#  Channels:
#    · EmailChannel  — sends HTML email via SMTP
#    · SlackChannel  — posts a rich Block Kit message to a webhook URL
# =============================================================================

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import psycopg2

logger = logging.getLogger(__name__)


# =============================================================================
#  AlertEvent — data class representing a price change notification
# =============================================================================

@dataclass
class AlertEvent:
    sku:            str
    product_name:   str
    source:         str
    url:            str
    prev_price:     float
    new_price:      float
    change_pct:     float
    currency:       str
    availability:   str
    scraped_at:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_price_drop(self) -> bool:
        return self.change_pct < 0

    @property
    def direction_label(self) -> str:
        return "📉 Price Drop" if self.is_price_drop else "📈 Price Increase"

    @property
    def change_abs(self) -> float:
        return abs(self.new_price - self.prev_price)

    @property
    def change_pct_abs(self) -> float:
        return abs(self.change_pct)


# =============================================================================
#  AlertManager
# =============================================================================

class AlertManager:
    """
    Coordinates price change detection and notification dispatch.
    Instantiated once per spider via AlertPipeline.
    """

    def __init__(self, db_settings: dict, settings):
        self.db_settings = db_settings

        # Thresholds
        self.drop_threshold     = float(os.environ.get("ALERT_PRICE_DROP_THRESHOLD",     5.0))
        self.increase_threshold = float(os.environ.get("ALERT_PRICE_INCREASE_THRESHOLD", 10.0))

        # Build enabled channels
        self.channels = []
        if os.environ.get("ALERT_EMAIL_ENABLED", "false").lower() == "true":
            self.channels.append(EmailChannel.from_env())

        if os.environ.get("ALERT_SLACK_ENABLED", "false").lower() == "true":
            self.channels.append(SlackChannel.from_env())

        if not self.channels:
            logger.info("AlertManager: no channels enabled — notifications disabled.")

    def check_and_alert(self, item) -> None:
        """
        Compare new price against previous snapshot.
        Dispatch alert if change exceeds configured thresholds.
        """
        if not self.channels:
            return

        sku    = item.get("sku")
        source = item.get("source")
        new_price = float(item.get("price_current") or 0)

        if not new_price:
            return

        prev_price = self._fetch_previous_price(sku, source)
        if prev_price is None:
            return   # First snapshot — no comparison possible

        if prev_price == 0:
            return

        change_pct = ((new_price - prev_price) / prev_price) * 100

        # Check thresholds
        triggered = (
            (change_pct < 0  and abs(change_pct) >= self.drop_threshold) or
            (change_pct > 0  and change_pct      >= self.increase_threshold)
        )

        if not triggered:
            return

        event = AlertEvent(
            sku          = sku,
            product_name = item.get("product_name") or sku,
            source       = source,
            url          = item.get("url") or "",
            prev_price   = prev_price,
            new_price    = new_price,
            change_pct   = round(change_pct, 2),
            currency     = item.get("currency") or "USD",
            availability = item.get("availability") or "unknown",
        )

        logger.info(
            "Price change detected: %s | %s → %s (%.1f%%) | dispatching alerts.",
            sku, prev_price, new_price, change_pct,
        )

        for channel in self.channels:
            try:
                channel.send(event)
            except Exception as e:
                logger.error("Alert channel %s failed: %s", type(channel).__name__, e)

    def _fetch_previous_price(self, sku: str, source: str) -> Optional[float]:
        """Fetch the second-to-last price snapshot for a given SKU + source."""
        try:
            conn = psycopg2.connect(**self.db_settings)
            cur  = conn.cursor()
            cur.execute("""
                SELECT ph.price_current
                FROM   pricing_history ph
                JOIN   products p ON p.product_id = ph.product_id
                JOIN   sources  s ON s.source_id  = p.source_id
                WHERE  p.sku    = %s
                  AND  s.name   = %s
                  AND  ph.price_current IS NOT NULL
                ORDER BY ph.scraped_at DESC
                OFFSET 1 LIMIT 1
            """, (sku, source))
            row = cur.fetchone()
            cur.close()
            conn.close()
            return float(row[0]) if row else None
        except Exception as e:
            logger.warning("Could not fetch previous price for SKU=%s: %s", sku, e)
            return None


# =============================================================================
#  EmailChannel
# =============================================================================

class EmailChannel:
    """Sends an HTML alert email via SMTP."""

    def __init__(self, smtp_host, smtp_port, smtp_user, smtp_password, sender, recipient):
        self.smtp_host     = smtp_host
        self.smtp_port     = int(smtp_port)
        self.smtp_user     = smtp_user
        self.smtp_password = smtp_password
        self.sender        = sender
        self.recipient     = recipient

    @classmethod
    def from_env(cls):
        return cls(
            smtp_host     = os.environ.get("ALERT_EMAIL_SMTP_HOST",     ""),
            smtp_port     = os.environ.get("ALERT_EMAIL_SMTP_PORT",     "587"),
            smtp_user     = os.environ.get("ALERT_EMAIL_SMTP_USER",     ""),
            smtp_password = os.environ.get("ALERT_EMAIL_SMTP_PASSWORD", ""),
            sender        = os.environ.get("ALERT_EMAIL_SENDER",        ""),
            recipient     = os.environ.get("ALERT_EMAIL_RECIPIENT",     ""),
        )

    def send(self, event: AlertEvent) -> None:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        subject = (
            f"[SentinelPrice] {event.direction_label} — "
            f"{event.product_name[:50]} ({event.change_pct:+.1f}%)"
        )

        html = self._build_html(event)
        msg  = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self.sender
        msg["To"]      = self.recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.sendmail(self.sender, self.recipient, msg.as_string())

        logger.info("Email alert sent to %s for SKU=%s", self.recipient, event.sku)

    def _build_html(self, event: AlertEvent) -> str:
        color      = "#d63031" if event.is_price_drop else "#e17055"
        bg_color   = "#fff5f5" if event.is_price_drop else "#fff9f0"
        arrow      = "▼" if event.is_price_drop else "▲"
        change_str = f"{arrow} {event.change_pct_abs:.1f}%"

        return f"""
        <!DOCTYPE html>
        <html>
        <body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
          <div style="max-width: 600px; margin: auto; background: white;
                      border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">

            <!-- Header -->
            <div style="background: {color}; padding: 20px 30px;">
              <h2 style="color: white; margin: 0;">{event.direction_label}</h2>
              <p style="color: rgba(255,255,255,0.85); margin: 4px 0 0;">
                Detected by SentinelPrice on {event.scraped_at.strftime('%Y-%m-%d %H:%M UTC')}
              </p>
            </div>

            <!-- Product -->
            <div style="padding: 24px 30px; background: {bg_color};">
              <h3 style="margin: 0 0 4px; color: #2d3436;">{event.product_name}</h3>
              <p style="margin: 0; color: #636e72; font-size: 13px;">
                Source: {event.source.upper()} &nbsp;·&nbsp; SKU: {event.sku}
              </p>
            </div>

            <!-- Price change -->
            <div style="padding: 24px 30px;">
              <table style="width: 100%; border-collapse: collapse;">
                <tr>
                  <td style="padding: 10px; background: #f8f9fa; border-radius: 6px; text-align: center; width: 30%;">
                    <div style="font-size: 12px; color: #b2bec3; margin-bottom: 4px;">PREVIOUS</div>
                    <div style="font-size: 22px; color: #636e72;">
                      {event.currency} {event.prev_price:.2f}
                    </div>
                  </td>
                  <td style="text-align: center; padding: 10px; width: 20%;">
                    <div style="font-size: 28px; color: {color};">{arrow}</div>
                  </td>
                  <td style="padding: 10px; background: #f8f9fa; border-radius: 6px; text-align: center; width: 30%;">
                    <div style="font-size: 12px; color: #b2bec3; margin-bottom: 4px;">NEW PRICE</div>
                    <div style="font-size: 22px; font-weight: bold; color: {color};">
                      {event.currency} {event.new_price:.2f}
                    </div>
                  </td>
                  <td style="text-align: center; padding: 10px; width: 20%;">
                    <div style="font-size: 18px; font-weight: bold; color: {color};">{change_str}</div>
                    <div style="font-size: 12px; color: #b2bec3;">
                      {event.currency} {event.change_abs:.2f}
                    </div>
                  </td>
                </tr>
              </table>

              <div style="margin-top: 16px;">
                <span style="background: #dfe6e9; padding: 4px 10px; border-radius: 20px;
                             font-size: 12px; color: #636e72;">
                  Availability: {event.availability}
                </span>
              </div>
            </div>

            <!-- CTA -->
            <div style="padding: 0 30px 24px;">
              <a href="{event.url}"
                 style="display: inline-block; background: {color}; color: white;
                        padding: 10px 20px; border-radius: 6px; text-decoration: none;
                        font-weight: bold; font-size: 14px;">
                View Product →
              </a>
            </div>

            <!-- Footer -->
            <div style="padding: 16px 30px; background: #f8f9fa; border-top: 1px solid #dfe6e9;">
              <p style="margin: 0; font-size: 11px; color: #b2bec3;">
                SentinelPrice · Price Intelligence Pipeline ·
                Thresholds: drop ≥ {os.environ.get('ALERT_PRICE_DROP_THRESHOLD', '5')}% /
                increase ≥ {os.environ.get('ALERT_PRICE_INCREASE_THRESHOLD', '10')}%
              </p>
            </div>

          </div>
        </body>
        </html>
        """


# =============================================================================
#  SlackChannel
# =============================================================================

class SlackChannel:
    """Posts a rich Block Kit message to a Slack Incoming Webhook URL."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    @classmethod
    def from_env(cls):
        return cls(webhook_url=os.environ.get("ALERT_SLACK_WEBHOOK_URL", ""))

    def send(self, event: AlertEvent) -> None:
        import urllib.request
        import json

        payload = json.dumps(self._build_blocks(event)).encode("utf-8")

        req = urllib.request.Request(
            self.webhook_url,
            data    = payload,
            headers = {"Content-Type": "application/json"},
            method  = "POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")

        logger.info("Slack alert sent for SKU=%s", event.sku)

    def _build_blocks(self, event: AlertEvent) -> dict:
        arrow    = "📉" if event.is_price_drop else "📈"
        color    = "#d63031" if event.is_price_drop else "#e17055"
        sign     = "-" if event.is_price_drop else "+"
        change_str = f"{sign}{event.change_pct_abs:.1f}%"

        return {
            "attachments": [
                {
                    "color": color,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": f"{arrow} {event.direction_label} Detected",
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*<{event.url}|{event.product_name}>*\n"
                                        f"Source: `{event.source.upper()}` · SKU: `{event.sku}`"
                            }
                        },
                        {"type": "divider"},
                        {
                            "type": "section",
                            "fields": [
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Previous Price*\n{event.currency} {event.prev_price:.2f}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*New Price*\n{event.currency} {event.new_price:.2f}"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Change*\n{change_str} ({event.currency} {event.change_abs:.2f})"
                                },
                                {
                                    "type": "mrkdwn",
                                    "text": f"*Availability*\n{event.availability}"
                                },
                            ]
                        },
                        {
                            "type": "context",
                            "elements": [
                                {
                                    "type": "mrkdwn",
                                    "text": (
                                        f"🕐 {event.scraped_at.strftime('%Y-%m-%d %H:%M UTC')} · "
                                        f"SentinelPrice · "
                                        f"Threshold: drop ≥{os.environ.get('ALERT_PRICE_DROP_THRESHOLD', '5')}% / "
                                        f"increase ≥{os.environ.get('ALERT_PRICE_INCREASE_THRESHOLD', '10')}%"
                                    )
                                }
                            ]
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "View Product →"},
                                    "url": event.url,
                                    "style": "primary" if event.is_price_drop else "danger",
                                }
                            ]
                        }
                    ]
                }
            ]
        }
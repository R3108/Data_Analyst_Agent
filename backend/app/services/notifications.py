"""Alert delivery: a watched number that moves is only useful if somebody hears about it.

Monitors and data contracts already decide *whether* something is wrong. This module
decides where that lands — a Slack webhook, any JSON endpoint, or an email — and keeps
a bounded log of what was sent, so a silent integration is visible rather than assumed.

Two rules keep it from becoming noise:

* **Transitions only.** A monitor that is still breached on the fortieth sweep has not
  changed; only crossing the line (and crossing back) is an event.
* **Explicit subscriptions.** Each channel names the events it wants.

Transport is injectable, so the whole path is unit-tested without a socket.
"""

from __future__ import annotations

import logging
import re
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Protocol

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database
from app.services import diagnosis

logger = logging.getLogger(__name__)

KINDS = ("slack", "webhook", "email")
EVENTS = ("breach", "recovery", "failure", "contract", "digest", "briefing")
MAX_CHANNELS = 20
DELIVERY_HISTORY = 200

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
SEVERITY_COLOR = {"critical": "#e34948", "warning": "#eda100", "info": "#2a78d6",
                  "good": "#1baf7a"}
SEVERITY_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵", "good": "🟢"}


@dataclass
class Alert:
    """One thing worth telling somebody, rendered per transport."""

    event: str
    title: str
    summary: str
    severity: str = "info"
    facts: list[tuple[str, str]] = field(default_factory=list)
    link: str | None = None
    link_label: str = "Open in Numera"

    def to_text(self) -> str:
        lines = [f"{SEVERITY_ICON.get(self.severity, '•')} *{self.title}*", self.summary]
        lines += [f"• {label}: {value}" for label, value in self.facts]
        if self.link:
            lines.append(f"<{self.link}|{self.link_label}>")
        return "\n".join(line for line in lines if line)

    def to_plain(self) -> str:
        lines = [self.title, "", self.summary]
        lines += [f"- {label}: {value}" for label, value in self.facts]
        if self.link:
            lines += ["", f"{self.link_label}: {self.link}"]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "source": "numera",
            "event": self.event,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "facts": [{"label": label, "value": value} for label, value in self.facts],
            "link": self.link,
        }


class Transport(Protocol):
    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        """Deliver, or raise. The return value is a short detail for the delivery log."""


class HttpTransport:
    """Slack-shaped for Slack webhooks, a plain structured document for everything else."""

    def __init__(self, timeout_s: float = 10.0) -> None:
        self.timeout_s = timeout_s

    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        import httpx  # imported lazily so the module loads without network deps installed

        payload = self._slack(alert) if channel["kind"] == "slack" else alert.to_json()
        response = httpx.post(channel["target"], json=payload, timeout=self.timeout_s)
        response.raise_for_status()
        return f"HTTP {response.status_code}"

    @staticmethod
    def _slack(alert: Alert) -> dict[str, Any]:
        fields = [
            {"type": "mrkdwn", "text": f"*{label}*\n{value}"} for label, value in alert.facts[:8]
        ]
        blocks: list[dict[str, Any]] = [
            {"type": "header", "text": {"type": "plain_text",
                                        "text": f"{SEVERITY_ICON.get(alert.severity, '')} {alert.title}"[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": alert.summary[:2900]}},
        ]
        if fields:
            blocks.append({"type": "section", "fields": fields})
        if alert.link:
            blocks.append({"type": "actions", "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": alert.link_label},
                "url": alert.link,
            }]})
        return {
            "text": f"{alert.title} — {alert.summary}"[:3000],
            "attachments": [{"color": SEVERITY_COLOR.get(alert.severity, "#2a78d6"),
                             "blocks": blocks}],
        }


class EmailTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        settings = self.settings
        if not settings.smtp_host:
            raise InvalidInputError(
                "Email alerts need SMTP_HOST (and usually SMTP_USER / SMTP_PASSWORD) in your .env."
            )
        message = EmailMessage()
        message["Subject"] = f"[Numera] {alert.title}"[:200]
        message["From"] = settings.smtp_from or settings.smtp_user or "numera@localhost"
        message["To"] = channel["target"]
        message.set_content(alert.to_plain())

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.alert_timeout_s) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.send_message(message)
        return f"sent to {channel['target']}"


class RoutingTransport:
    def __init__(self, settings: Settings) -> None:
        self.http = HttpTransport(settings.alert_timeout_s)
        self.email = EmailTransport(settings)

    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        return (self.email if channel["kind"] == "email" else self.http).send(channel, alert)


class NotificationService:
    def __init__(self, settings: Settings, db: Database, transport: Transport | None = None) -> None:
        self.settings = settings
        self.db = db
        self.transport = transport or RoutingTransport(settings)

    # ------------------------------------------------------------------ channels
    def list(self) -> list[dict[str, Any]]:
        return self.db.list_channels()

    def require(self, channel_id: str) -> dict[str, Any]:
        channel = self.db.get_channel(channel_id)
        if channel is None:
            raise NotFoundError(f"Alert channel '{channel_id}' was not found.")
        return channel

    def create(self, *, name: str, kind: str, target: str, events: list[str] | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise InvalidInputError(f"Unknown channel type '{kind}'.")
        if len(self.db.list_channels()) >= MAX_CHANNELS:
            raise InvalidInputError(f"You can keep at most {MAX_CHANNELS} alert channels.")
        return self.db.create_channel({
            "name": (name or _default_name(kind, target)).strip()[:120],
            "kind": kind,
            "target": _validate_target(kind, target),
            "events": ",".join(_validate_events(events)),
        })

    def update(self, channel_id: str, *, name: str | None = None, target: str | None = None,
               events: list[str] | None = None, enabled: bool | None = None) -> dict[str, Any]:
        channel = self.require(channel_id)
        self.db.update_channel(
            channel_id,
            name=name.strip()[:120] if name else None,
            target=_validate_target(channel["kind"], target) if target else None,
            events=",".join(_validate_events(events)) if events is not None else None,
            enabled=None if enabled is None else int(enabled),
        )
        return self.require(channel_id)

    def delete(self, channel_id: str) -> None:
        if not self.db.delete_channel(channel_id):
            raise NotFoundError(f"Alert channel '{channel_id}' was not found.")

    def test(self, channel_id: str) -> dict[str, Any]:
        channel = self.require(channel_id)
        alert = Alert(
            event="test",
            title="Numera test alert",
            summary="If you can read this, alerts from this workspace will reach you.",
            severity="good",
            facts=[("Channel", channel["name"]), ("Delivers", ", ".join(channel["events"]) or "nothing yet")],
            link=self.settings.public_base_url or None,
        )
        return self._deliver(channel, alert)

    def deliveries(self, limit: int = 30) -> list[dict[str, Any]]:
        return self.db.list_deliveries(limit)

    # ------------------------------------------------------------------ dispatch
    def notify(self, alert: Alert) -> list[dict[str, Any]]:
        """Send to every enabled channel subscribed to this event. Never raises."""
        if not self.settings.alerts_enabled:
            return []
        targets = [
            c for c in self.db.list_channels() if c["enabled"] and alert.event in c["events"]
        ]
        return [self._deliver(channel, alert, swallow=True) for channel in targets]

    def _deliver(self, channel: dict[str, Any], alert: Alert, swallow: bool = False) -> dict[str, Any]:
        try:
            detail = self.transport.send(channel, alert)
            status = "sent"
        except Exception as exc:  # noqa: BLE001 — a dead webhook must not break the sweep
            status, detail = "failed", _reason(exc)
            logger.warning("Alert to %s failed: %s", channel["id"], detail)
            if not swallow:
                self._record(channel, alert, status, detail)
                raise InvalidInputError(f"Delivery failed: {detail}") from exc
        return self._record(channel, alert, status, detail)

    def _record(self, channel: dict[str, Any], alert: Alert, status: str, detail: str) -> dict[str, Any]:
        delivery = self.db.add_delivery({
            "channel_id": channel["id"], "event": alert.event, "title": alert.title,
            "status": status, "detail": detail[:400],
        }, history_limit=DELIVERY_HISTORY)
        self.db.update_channel(
            channel["id"],
            last_status=status,
            last_error=detail[:400] if status == "failed" else "",
            last_sent_at=delivery["created_at"],
            sent_count=int(channel.get("sent_count") or 0) + 1,
        )
        return {**delivery, "channel_name": channel["name"], "channel_kind": channel["kind"]}

    # ------------------------------------------------------------------ alert builders
    def monitor_alert(self, monitor: dict[str, Any], run: dict[str, Any],
                      previous_status: str | None) -> Alert | None:
        """One alert per state change — never a reminder that nothing has changed."""
        status = run["status"]
        if status == previous_status:
            return None
        link = self._link("?view=monitors")
        facts = [
            ("Metric", monitor["title"]),
            ("Value", monitor.get("formatted_value") or "—"),
            ("Rule", monitor.get("rule") or ""),
            ("Dataset", monitor.get("dataset_name") or "—"),
        ]
        if status == "breached":
            # A breach alert that cannot say why is a page at 3am with no next step, so
            # the deterministic drill-down rides along when there is one.
            cause = run.get("root_cause") or {}
            summary = run.get("detail") or "The monitor breached its threshold."
            if cause.get("status") == "ok":
                summary = f"{summary}\n{cause['summary']}"
            return Alert("breach", f"{monitor['title']} is outside its range", summary,
                         "critical", facts + diagnosis.alert_facts(cause), link)
        if status == "error":
            return Alert("failure", f"{monitor['title']} could not be checked",
                         run.get("detail") or "The snapshotted analysis no longer runs.",
                         "warning", facts, link)
        if status == "ok" and previous_status in ("breached", "error"):
            return Alert("recovery", f"{monitor['title']} is back within range",
                         run.get("detail") or "The metric returned to its agreed range.",
                         "good", facts, link)
        return None

    def contract_alert(self, dataset: dict[str, Any], result: dict[str, Any]) -> Alert | None:
        if not result or result["status"] not in ("fail", "warn"):
            return None
        counts = result["counts"]
        facts = [
            ("Dataset", f"{dataset.get('name', 'dataset')} v{dataset.get('version', 1)}"),
            ("Failed", str(counts["fail"])),
            ("Warned", str(counts["warn"])),
            ("Passed", str(counts["pass"])),
        ]
        facts += [("Broken", f["description"]) for f in result.get("failures", [])[:3]]
        return Alert(
            "contract",
            f"Data contract {'failed' if result['status'] == 'fail' else 'warned'} for "
            f"{dataset.get('name', 'a dataset')}",
            result["headline"],
            "critical" if result["status"] == "fail" else "warning",
            facts,
            self._link(""),
        )

    def digest_alert(self, digest: dict[str, Any]) -> Alert:
        counts = digest["counts"]
        breached = digest.get("breached") or []
        summary = (
            f"{digest['total']} monitor(s): {counts['breached']} breached, "
            f"{counts['ok']} within range, {counts['error']} failing."
        )
        facts = []
        for monitor in breached[:6]:
            cause = monitor.get("root_cause") or {}
            detail = monitor.get("last_detail") or monitor["rule"]
            if cause.get("status") == "ok":
                detail = f"{detail}\n{cause['summary']}"
            facts.append((monitor["title"], f"{monitor['formatted_value']} — {detail}"))
        return Alert(
            "digest",
            "Numera monitor briefing",
            summary,
            "critical" if counts["breached"] else "info",
            facts or [("Status", "Everything is within range.")],
            self._link("?view=monitors"),
            link_label="Open monitors",
        )

    def _link(self, suffix: str) -> str | None:
        base = (self.settings.public_base_url or "").rstrip("/")
        return f"{base}{suffix}" if base else None


# --------------------------------------------------------------------------- helpers


def _validate_events(events: list[str] | None) -> list[str]:
    if events is None:
        return ["breach", "failure", "contract"]
    chosen = [e for e in dict.fromkeys(events) if e in EVENTS]
    if not chosen:
        raise InvalidInputError(f"Pick at least one event from: {', '.join(EVENTS)}.")
    return chosen


def _validate_target(kind: str, target: str | None) -> str:
    value = (target or "").strip()
    if not value:
        raise InvalidInputError("A channel needs a destination.")
    if kind == "email":
        if not EMAIL_RE.match(value):
            raise InvalidInputError(f"'{value}' is not a valid email address.")
        return value[:320]
    if not value.lower().startswith(("http://", "https://")):
        raise InvalidInputError("A webhook destination must be an http(s) URL.")
    return value[:2000]


def _default_name(kind: str, target: str) -> str:
    if kind == "email":
        return target
    host = re.sub(r"^https?://", "", target or "").split("/")[0]
    return f"{kind.title()} · {host}"[:120]


def _reason(exc: Exception) -> str:
    """A short, safe description — never the request body, which may hold a secret URL."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status:
        return f"HTTP {status}"
    message = str(exc).strip() or exc.__class__.__name__
    return re.sub(r"https?://\S+", "<url>", message)[:200]

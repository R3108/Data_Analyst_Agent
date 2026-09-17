"""Alert delivery: subscriptions, transitions, redaction and failure isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database
from app.services.notifications import Alert, HttpTransport, NotificationService


class RecordingTransport:
    """Captures what would have gone out; raises for targets marked `fail`."""

    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], Alert]] = []

    def send(self, channel: dict[str, Any], alert: Alert) -> str:
        if "fail" in channel["target"]:
            raise RuntimeError(f"connection refused to {channel['target']}")
        self.sent.append((channel, alert))
        return "HTTP 200"

    @property
    def events(self) -> list[str]:
        return [alert.event for _, alert in self.sent]


@pytest.fixture
def notifier(tmp_path: Path) -> NotificationService:
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path,
                        public_base_url="https://numera.example")
    return NotificationService(settings, Database(tmp_path / "test.db"), RecordingTransport())


@pytest.fixture
def transport(notifier: NotificationService) -> RecordingTransport:
    return notifier.transport  # type: ignore[return-value]


def slack(notifier: NotificationService, events: list[str] | None = None) -> dict[str, Any]:
    return notifier.create(name="Ops", kind="slack", target="https://hooks.slack.com/services/x",
                           events=events)


# --------------------------------------------------------------------------- channels


def test_channel_defaults_and_validation(notifier: NotificationService) -> None:
    channel = slack(notifier)
    assert channel["events"] == ["breach", "failure", "contract"]
    assert channel["enabled"] is True
    assert channel["sent_count"] == 0

    with pytest.raises(InvalidInputError, match="http"):
        notifier.create(name="Bad", kind="webhook", target="ftp://nope")
    with pytest.raises(InvalidInputError, match="valid email"):
        notifier.create(name="Bad", kind="email", target="not-an-email")
    with pytest.raises(InvalidInputError, match="Unknown channel type"):
        notifier.create(name="Bad", kind="carrier-pigeon", target="https://x.test")
    with pytest.raises(InvalidInputError, match="at least one event"):
        notifier.create(name="Bad", kind="webhook", target="https://x.test", events=["nonsense"])


def test_channels_are_named_after_their_destination(notifier: NotificationService) -> None:
    channel = notifier.create(name="", kind="webhook", target="https://hooks.example.com/a/b")
    assert channel["name"] == "Webhook · hooks.example.com"


def test_update_and_delete(notifier: NotificationService) -> None:
    channel = slack(notifier)
    updated = notifier.update(channel["id"], name="Renamed", events=["digest"], enabled=False)
    assert updated["name"] == "Renamed"
    assert updated["events"] == ["digest"]
    assert updated["enabled"] is False

    notifier.delete(channel["id"])
    with pytest.raises(NotFoundError):
        notifier.require(channel["id"])


# --------------------------------------------------------------------------- dispatch


def test_only_subscribed_enabled_channels_receive(
    notifier: NotificationService, transport: RecordingTransport
) -> None:
    slack(notifier, ["breach"])
    notifier.create(name="Digest only", kind="webhook", target="https://x.test/d", events=["digest"])
    paused = notifier.create(name="Paused", kind="webhook", target="https://x.test/p", events=["breach"])
    notifier.update(paused["id"], enabled=False)

    deliveries = notifier.notify(Alert("breach", "Revenue dropped", "It fell 20%.", "critical"))
    assert len(deliveries) == 1
    assert [c["name"] for c, _ in transport.sent] == ["Ops"]


def test_one_dead_channel_does_not_block_the_others(
    notifier: NotificationService, transport: RecordingTransport
) -> None:
    notifier.create(name="Dead", kind="webhook", target="https://fail.test/hook", events=["breach"])
    slack(notifier, ["breach"])

    deliveries = notifier.notify(Alert("breach", "Revenue dropped", "It fell 20%."))
    assert [d["status"] for d in deliveries] == ["failed", "sent"]
    assert len(transport.sent) == 1


def test_failure_detail_never_leaks_the_webhook_url(notifier: NotificationService) -> None:
    channel = notifier.create(name="Dead", kind="webhook", target="https://fail.test/secret-token",
                              events=["breach"])
    notifier.notify(Alert("breach", "x", "y"))
    assert "secret-token" not in (notifier.require(channel["id"])["last_error"] or "")
    assert notifier.deliveries()[0]["detail"] == "connection refused to <url>"


def test_delivery_log_and_counters(notifier: NotificationService) -> None:
    channel = slack(notifier, ["breach"])
    notifier.notify(Alert("breach", "First", "a"))
    notifier.notify(Alert("breach", "Second", "b"))

    refreshed = notifier.require(channel["id"])
    assert refreshed["sent_count"] == 2
    assert refreshed["last_status"] == "sent"
    assert [d["title"] for d in notifier.deliveries()] == ["Second", "First"]


def test_alerts_can_be_switched_off_globally(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, environment="test", data_dir=tmp_path, alerts_enabled=False)
    notifier = NotificationService(settings, Database(tmp_path / "off.db"), RecordingTransport())
    slack(notifier, ["breach"])
    assert notifier.notify(Alert("breach", "x", "y")) == []


def test_a_failing_test_send_is_reported_to_the_caller(notifier: NotificationService) -> None:
    channel = notifier.create(name="Dead", kind="webhook", target="https://fail.test/h", events=["breach"])
    with pytest.raises(InvalidInputError, match="Delivery failed"):
        notifier.test(channel["id"])
    # ...and still logged, so the UI can show what went wrong.
    assert notifier.deliveries()[0]["status"] == "failed"


def test_test_alert_reaches_a_healthy_channel(
    notifier: NotificationService, transport: RecordingTransport
) -> None:
    channel = slack(notifier, ["digest"])
    delivery = notifier.test(channel["id"])
    assert delivery["status"] == "sent"
    assert transport.events == ["test"]  # a test ignores the subscription list


# --------------------------------------------------------------------------- alert builders


def monitor(**overrides: Any) -> dict[str, Any]:
    return {"id": "mon_1", "title": "Total revenue", "formatted_value": "$1,200",
            "rule": "alert below $2,000", "dataset_name": "Retail", **overrides}


def test_monitor_alerts_fire_only_on_a_state_change(notifier: NotificationService) -> None:
    run = {"status": "breached", "detail": "1,200 has fallen below 2,000."}
    assert notifier.monitor_alert(monitor(), run, previous_status="ok").event == "breach"
    assert notifier.monitor_alert(monitor(), run, previous_status="breached") is None


def test_recovery_and_failure_alerts(notifier: NotificationService) -> None:
    recovery = notifier.monitor_alert(monitor(), {"status": "ok", "detail": "back"}, "breached")
    assert recovery.event == "recovery" and recovery.severity == "good"
    # A first-ever "ok" is not a recovery — there was nothing to recover from.
    assert notifier.monitor_alert(monitor(), {"status": "ok", "detail": "fine"}, None) is None

    failure = notifier.monitor_alert(monitor(), {"status": "error", "detail": "no column"}, "ok")
    assert failure.event == "failure" and failure.severity == "warning"


def test_alerts_carry_a_link_back_to_the_workspace(notifier: NotificationService) -> None:
    alert = notifier.monitor_alert(monitor(), {"status": "breached", "detail": "x"}, "ok")
    assert alert.link == "https://numera.example?view=monitors"


def test_contract_alert_only_for_a_broken_contract(notifier: NotificationService) -> None:
    dataset = {"name": "Retail", "version": 3}
    passing = {"status": "pass", "counts": {"pass": 9, "warn": 0, "fail": 0}, "headline": "ok",
               "failures": []}
    assert notifier.contract_alert(dataset, passing) is None

    failing = {"status": "fail", "counts": {"pass": 7, "warn": 1, "fail": 2},
               "headline": "2 of 10 contract checks failed",
               "failures": [{"description": "`revenue` is present"}]}
    alert = notifier.contract_alert(dataset, failing)
    assert alert.severity == "critical"
    assert "Retail" in alert.title
    assert ("Dataset", "Retail v3") in alert.facts
    assert ("Broken", "`revenue` is present") in alert.facts


def test_digest_alert_summarises_the_breaches(notifier: NotificationService) -> None:
    digest = {"total": 3, "counts": {"ok": 2, "breached": 1, "error": 0, "pending": 0},
              "breached": [monitor(last_detail="fell below 2,000")]}
    alert = notifier.digest_alert(digest)
    assert alert.event == "digest" and alert.severity == "critical"
    assert "1 breached" in alert.summary
    assert alert.facts[0][0] == "Total revenue"


def test_quiet_digest_still_says_something(notifier: NotificationService) -> None:
    digest = {"total": 2, "counts": {"ok": 2, "breached": 0, "error": 0, "pending": 0}, "breached": []}
    alert = notifier.digest_alert(digest)
    assert alert.severity == "info"
    assert alert.facts == [("Status", "Everything is within range.")]


# --------------------------------------------------------------------------- rendering


def test_slack_payload_shape() -> None:
    alert = Alert("breach", "Revenue dropped", "It fell 20%.", "critical",
                  [("Metric", "Revenue"), ("Value", "$1.2K")], "https://numera.example")
    payload = HttpTransport._slack(alert)
    assert payload["text"].startswith("Revenue dropped")
    blocks = payload["attachments"][0]["blocks"]
    assert blocks[0]["type"] == "header"
    assert "Revenue dropped" in blocks[0]["text"]["text"]
    assert blocks[2]["fields"][0]["text"] == "*Metric*\nRevenue"
    assert blocks[3]["elements"][0]["url"] == "https://numera.example"
    assert payload["attachments"][0]["color"] == "#e34948"


def test_generic_webhook_payload_is_structured() -> None:
    alert = Alert("contract", "Contract failed", "2 checks failed", "critical",
                  [("Dataset", "Retail v3")])
    body = alert.to_json()
    assert body["source"] == "numera"
    assert body["event"] == "contract"
    assert body["facts"] == [{"label": "Dataset", "value": "Retail v3"}]


def test_email_body_is_plain_text() -> None:
    alert = Alert("breach", "Revenue dropped", "It fell 20%.", "critical",
                  [("Metric", "Revenue")], "https://numera.example")
    plain = alert.to_plain()
    assert plain.startswith("Revenue dropped")
    assert "- Metric: Revenue" in plain
    assert "Open in Numera: https://numera.example" in plain
    assert "<" not in plain  # no Slack link markup leaking into an email

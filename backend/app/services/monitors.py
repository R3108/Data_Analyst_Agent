"""Metric monitors: keep watching a number after the conversation ends.

A monitor snapshots the *code* that produced a KPI, so re-checking it costs one
sandbox run and zero model tokens. Monitors re-run on demand, on an optional
schedule, and automatically whenever a new version of their dataset is uploaded —
which is what turns a one-off answer into an ongoing KPI watch.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database
from app.services.datasets import DatasetService

logger = logging.getLogger(__name__)

DIRECTIONS = ("above", "below", "change_pct")
MAX_MONITORS = 60


class MonitorService:
    def __init__(self, settings: Settings, db: Database, datasets: DatasetService, runner: Any,
                 notifier: Any | None = None) -> None:
        self.settings = settings
        self.db = db
        self.datasets = datasets
        self.runner = runner
        self.notifier = notifier

    # ------------------------------------------------------------------ reads
    def list(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        return [self._decorate(monitor) for monitor in self.db.list_monitors(dataset_id)]

    def get(self, monitor_id: str) -> dict[str, Any]:
        return self._decorate(self.require(monitor_id))

    def require(self, monitor_id: str) -> dict[str, Any]:
        monitor = self.db.get_monitor(monitor_id)
        if monitor is None:
            raise NotFoundError(f"Monitor '{monitor_id}' was not found.")
        return monitor

    # ------------------------------------------------------------------ writes
    def create(
        self,
        *,
        message_id: str,
        index: int = 0,
        direction: str,
        threshold: float,
        title: str | None = None,
    ) -> dict[str, Any]:
        if direction not in DIRECTIONS:
            raise InvalidInputError(f"Unknown monitor direction '{direction}'.")
        if len(self.db.list_monitors()) >= MAX_MONITORS:
            raise InvalidInputError(f"You can keep at most {MAX_MONITORS} monitors.")

        message = self.db.get_message(message_id)
        if message is None or message["role"] != "assistant":
            raise NotFoundError("The analysis you tried to monitor no longer exists.")
        payload = message.get("payload") or {}
        kpis = ((payload.get("execution") or {}).get("kpis")) or []
        if not 0 <= index < len(kpis):
            raise InvalidInputError("That KPI is not available in this analysis.")
        code = payload.get("code")
        if not code:
            raise InvalidInputError(
                "This answer was produced without analysis code, so it cannot be re-checked."
            )
        kpi = kpis[index]
        if not isinstance(kpi.get("value"), (int, float)) or isinstance(kpi.get("value"), bool):
            raise InvalidInputError("Only numeric KPIs can be monitored.")

        session = self.db.get_session(message["session_id"])
        if session is None:
            raise NotFoundError("The analysis you tried to monitor no longer exists.")

        monitor = self.db.create_monitor({
            "title": (title or kpi["label"]).strip()[:200],
            "dataset_id": session["dataset_id"],
            "source_session_id": message["session_id"],
            "question": self.db.question_before(message),
            "kpi_label": kpi["label"],
            "kpi_index": int(index),
            "kpi_format": kpi.get("format") or "auto",
            "code": code,
            "direction": direction,
            "threshold": float(threshold),
            "baseline_value": float(kpi["value"]),
            "last_value": float(kpi["value"]),
            "last_status": "ok",
        })
        return self._decorate(monitor)

    def update(
        self,
        monitor_id: str,
        *,
        title: str | None = None,
        direction: str | None = None,
        threshold: float | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        self.require(monitor_id)
        if direction is not None and direction not in DIRECTIONS:
            raise InvalidInputError(f"Unknown monitor direction '{direction}'.")
        self.db.update_monitor(
            monitor_id,
            title=title.strip()[:200] if title else None,
            direction=direction,
            threshold=threshold,
            # `enabled` is stored as an integer so False survives the "is not None" filter.
            enabled=None if enabled is None else int(enabled),
        )
        return self.get(monitor_id)

    def delete(self, monitor_id: str) -> None:
        if not self.db.delete_monitor(monitor_id):
            raise NotFoundError(f"Monitor '{monitor_id}' was not found.")

    # ------------------------------------------------------------------ evaluation
    def run(self, monitor_id: str) -> dict[str, Any]:
        """Re-execute the snapshotted code and record one observation."""
        monitor = self.require(monitor_id)
        target = self.db.latest_dataset_version(monitor["dataset_id"])
        if target is None:
            return self._record(monitor, status="error", detail="The dataset no longer exists.")

        try:
            path = self.datasets.data_path(target["id"])
        except NotFoundError as exc:
            return self._record(monitor, status="error", dataset_id=target["id"], detail=exc.message)

        result = self.runner.run(monitor["code"], Path(path))
        if not result.ok:
            detail = (result.error or "The analysis failed.").splitlines()[0][:300]
            return self._record(monitor, status="error", dataset_id=target["id"],
                                detail=detail, duration_ms=result.duration_ms)

        kpi = self._find_kpi(result.kpis, monitor)
        if kpi is None:
            return self._record(
                monitor, status="error", dataset_id=target["id"], duration_ms=result.duration_ms,
                detail=f"The analysis no longer produces a KPI called “{monitor['kpi_label']}”.",
            )

        value = float(kpi["value"])
        previous = monitor.get("last_value")
        change_pct = None
        if isinstance(previous, (int, float)) and previous:
            change_pct = (value - float(previous)) / abs(float(previous))

        breached, detail = self._evaluate(monitor, value, change_pct)
        return self._record(
            monitor, status="breached" if breached else "ok", dataset_id=target["id"],
            value=value, previous_value=previous, change_pct=change_pct, breached=breached,
            detail=detail, duration_ms=result.duration_ms,
        )

    def run_many(self, monitor_ids: list[str]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for monitor_id in monitor_ids:
            try:
                results.append(self.run(monitor_id))
            except Exception:  # noqa: BLE001 — one bad monitor must not stop the sweep
                logger.warning("Monitor %s failed to run", monitor_id, exc_info=True)
        return results

    def run_all(self, dataset_id: str | None = None) -> list[dict[str, Any]]:
        monitors = [m for m in self.db.list_monitors(dataset_id) if m["enabled"]]
        return self.run_many([m["id"] for m in monitors])

    def run_for_lineage(self, dataset_id: str) -> list[dict[str, Any]]:
        """Re-check every monitor whose dataset is part of the same version lineage."""
        target = self.db.get_dataset(dataset_id)
        if target is None:
            return []
        root = target.get("root_dataset_id") or target["id"]
        monitors = [
            m for m in self.db.list_monitors()
            if m["enabled"] and (m.get("root_dataset_id") or m["dataset_id"]) == root
        ]
        return self.run_many([m["id"] for m in monitors])

    # ------------------------------------------------------------------ digest
    def digest(self) -> dict[str, Any]:
        monitors = self.list()
        counts = {"ok": 0, "breached": 0, "error": 0, "pending": 0}
        for monitor in monitors:
            counts[monitor["last_status"] if monitor["last_status"] in counts else "pending"] += 1
        return {
            "total": len(monitors),
            "counts": counts,
            "breached": [m for m in monitors if m["last_status"] == "breached"],
            "monitors": monitors,
            "interval_minutes": self.settings.monitor_interval_minutes,
        }

    def digest_markdown(self) -> str:
        digest = self.digest()
        counts = digest["counts"]
        lines = [
            "# Monitor briefing",
            "",
            f"_{digest['total']} monitor(s) · {counts['breached']} breached · "
            f"{counts['ok']} within range · {counts['error']} failing_",
            "",
        ]
        if not digest["monitors"]:
            lines.append("No monitors yet. Watch a KPI from any analysis to start tracking it.")
            return "\n".join(lines)

        for status, heading in (("breached", "## Needs attention"), ("error", "## Failing checks"),
                                ("ok", "## Within range")):
            group = [m for m in digest["monitors"] if m["last_status"] == status]
            if not group:
                continue
            lines += [heading, ""]
            for monitor in group:
                lines.append(
                    f"- **{monitor['title']}** — {monitor['formatted_value']} "
                    f"({monitor['rule']}){' · ' + monitor['last_detail'] if monitor.get('last_detail') else ''}"
                )
            lines.append("")
        pending = [m for m in digest["monitors"] if m["last_status"] not in ("ok", "breached", "error")]
        if pending:
            lines += ["## Not yet checked", ""] + [f"- {m['title']}" for m in pending] + [""]
        return "\n".join(lines)

    # ------------------------------------------------------------------ internals
    @staticmethod
    def _find_kpi(kpis: list[dict[str, Any]], monitor: dict[str, Any]) -> dict[str, Any] | None:
        label = str(monitor["kpi_label"]).casefold()
        candidates = [
            k for k in kpis
            if str(k.get("label", "")).casefold() == label
            and isinstance(k.get("value"), (int, float)) and not isinstance(k.get("value"), bool)
        ]
        if candidates:
            return candidates[0]
        # Fall back to position if the label was reworded but the shape is unchanged.
        index = int(monitor.get("kpi_index") or 0)
        if 0 <= index < len(kpis):
            kpi = kpis[index]
            if isinstance(kpi.get("value"), (int, float)) and not isinstance(kpi.get("value"), bool):
                return kpi
        return None

    @staticmethod
    def _evaluate(
        monitor: dict[str, Any], value: float, change_pct: float | None
    ) -> tuple[bool, str]:
        threshold = float(monitor["threshold"])
        direction = monitor["direction"]
        if direction == "above":
            breached = value > threshold
            return breached, (f"{value:,.4g} is above the limit of {threshold:,.4g}." if breached
                              else f"{value:,.4g} is within the limit of {threshold:,.4g}.")
        if direction == "below":
            breached = value < threshold
            return breached, (f"{value:,.4g} has fallen below {threshold:,.4g}." if breached
                              else f"{value:,.4g} is at or above {threshold:,.4g}.")
        if change_pct is None:
            return False, f"Baseline recorded at {value:,.4g}."
        breached = abs(change_pct) >= threshold
        movement = "rose" if change_pct > 0 else "fell"
        return breached, (
            f"{movement} {abs(change_pct):.1%} since the last check"
            f"{f' (limit {threshold:.1%})' if breached else ''}."
        )

    def _record(
        self,
        monitor: dict[str, Any],
        *,
        status: str,
        dataset_id: str | None = None,
        value: float | None = None,
        previous_value: float | None = None,
        change_pct: float | None = None,
        breached: bool = False,
        detail: str | None = None,
        duration_ms: int | None = None,
    ) -> dict[str, Any]:
        previous_status = monitor.get("last_status")
        run = self.db.add_monitor_run(
            monitor["id"],
            {"dataset_id": dataset_id, "value": value, "previous_value": previous_value,
             "change_pct": change_pct, "status": status, "breached": breached, "detail": detail,
             "duration_ms": duration_ms},
            history_limit=self.settings.monitor_history_limit,
        )
        self.db.update_monitor(
            monitor["id"],
            last_value=value,
            last_status=status,
            last_run_at=run["created_at"],
            baseline_value=None if monitor.get("baseline_value") is not None else value,
        )
        updated = self.get(monitor["id"])
        self._announce(updated, run, previous_status)
        return {"monitor": updated, "run": run}

    def _announce(self, monitor: dict[str, Any], run: dict[str, Any],
                  previous_status: str | None) -> None:
        """Tell the configured channels — but only when the state actually changed."""
        if self.notifier is None:
            return
        try:
            alert = self.notifier.monitor_alert(monitor, run, previous_status)
            if alert is not None:
                self.notifier.notify(alert)
        except Exception:  # noqa: BLE001 — delivery must never fail a check
            logger.warning("Could not announce monitor %s", monitor["id"], exc_info=True)

    def _decorate(self, monitor: dict[str, Any]) -> dict[str, Any]:
        runs = self.db.list_monitor_runs(monitor["id"], limit=30)
        history = [r["value"] for r in runs if isinstance(r["value"], (int, float))]
        latest = runs[-1] if runs else None
        return {
            **monitor,
            "runs": runs,
            "history": history,
            "last_detail": (latest or {}).get("detail"),
            "last_change_pct": (latest or {}).get("change_pct"),
            "formatted_value": _format(monitor.get("last_value"), monitor.get("kpi_format")),
            "rule": _rule_text(monitor),
        }


def _format(value: Any, fmt: str | None) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "—"
    number = float(value)
    if fmt == "percent":
        return f"{number * 100:,.1f}%"
    if fmt == "currency":
        return f"${number:,.0f}" if abs(number) >= 1000 else f"${number:,.2f}"
    if fmt == "integer" or number.is_integer():
        return f"{number:,.0f}"
    return f"{number:,.2f}"


def _rule_text(monitor: dict[str, Any]) -> str:
    threshold, fmt = float(monitor["threshold"]), monitor.get("kpi_format")
    if monitor["direction"] == "above":
        return f"alert above {_format(threshold, fmt)}"
    if monitor["direction"] == "below":
        return f"alert below {_format(threshold, fmt)}"
    return f"alert on a {threshold:.0%} move"

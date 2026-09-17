"""Workspace activity: what happened here, in order, and who did it.

Two people sharing a workspace need to know that the dataset under a board was replaced
this morning, that a monitor breached overnight and that somebody already asked the
question they are about to ask. None of that is visible from a list of analyses sorted by
date.

The log is append-only, bounded, and never a source of truth for state — every entry is a
description of something that already succeeded elsewhere. Recording one must therefore
never be able to fail the action it describes, which is why every call here swallows its
own errors.
"""

from __future__ import annotations

import logging
from typing import Any

from app.db import Database

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 500

# action → (icon hint, human template). The UI renders the icon; the server owns the words
# so an activity feed and an alert describe the same event the same way.
ACTIONS: dict[str, dict[str, str]] = {
    "dataset.upload": {"icon": "upload", "verb": "uploaded"},
    "dataset.version": {"icon": "upload", "verb": "uploaded a new version of"},
    "dataset.delete": {"icon": "trash", "verb": "deleted"},
    "source.sync": {"icon": "database", "verb": "synced"},
    "source.create": {"icon": "database", "verb": "connected"},
    "analysis.run": {"icon": "message", "verb": "asked"},
    "investigation.run": {"icon": "compass", "verb": "investigated"},
    "briefing.run": {"icon": "clock", "verb": "ran the scheduled briefing"},
    "briefing.create": {"icon": "clock", "verb": "scheduled"},
    "board.pin": {"icon": "pin", "verb": "pinned to"},
    "board.share": {"icon": "share", "verb": "shared"},
    "board.unshare": {"icon": "share", "verb": "revoked the link for"},
    "session.share": {"icon": "share", "verb": "shared"},
    "session.unshare": {"icon": "share", "verb": "revoked the link for"},
    "monitor.create": {"icon": "bell", "verb": "started watching"},
    "monitor.breach": {"icon": "bell", "verb": "breached:"},
    "monitor.recovery": {"icon": "bell", "verb": "recovered:"},
    "contract.fail": {"icon": "shield", "verb": "broke its data contract:"},
    "comment.add": {"icon": "comment", "verb": "commented on"},
}


class ActivityService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def record(
        self,
        action: str,
        *,
        actor: str,
        subject_kind: str | None = None,
        subject_id: str | None = None,
        subject_title: str | None = None,
        detail: str | None = None,
    ) -> dict[str, Any] | None:
        """Log an event. Returns None if logging failed — the caller carries on regardless."""
        try:
            return self._decorate(self.db.add_activity({
                "actor": actor,
                "action": action,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "subject_title": (subject_title or "")[:200] or None,
                "detail": (detail or "")[:400] or None,
            }, history_limit=HISTORY_LIMIT))
        except Exception:  # noqa: BLE001 — an audit line must never break the audited action
            logger.warning("Could not record activity '%s'", action, exc_info=True)
            return None

    def list(self, limit: int = 50, subject_id: str | None = None) -> list[dict[str, Any]]:
        return [self._decorate(e) for e in self.db.list_activity(limit=limit, subject_id=subject_id)]

    @staticmethod
    def _decorate(entry: dict[str, Any]) -> dict[str, Any]:
        meta = ACTIONS.get(entry["action"], {"icon": "dot", "verb": entry["action"]})
        title = entry.get("subject_title") or ""
        return {
            **entry,
            "icon": meta["icon"],
            "summary": f"{entry['actor']} {meta['verb']} {title}".strip(),
        }


__all__ = ["ACTIONS", "ActivityService"]

"""Boards: live dashboards assembled from pinned analysis results."""

from __future__ import annotations

from typing import Any

from typing import Callable

import pandas as pd

from app.core.errors import InvalidInputError, NotFoundError
from app.db import Database
from app.services import cohorts as cohorts_service
from app.services import drivers as drivers_service
from app.services import forecasting as forecasting_service
from app.services import scenarios as scenarios_service
from app.services import statistics as statistics_service

# kind -> (payload section, list key)
PINNABLE: dict[str, tuple[str, str]] = {
    "kpi": ("execution", "kpis"),
    "chart": ("execution", "charts"),
    "table": ("execution", "tables"),
    "insight": ("report", "insights"),
}
# The deterministic views are not attached to a message, so a pin from one names the
# dataset and the parameters and the server recomputes the result before snapshotting it.
# Clients never supply pinned content — that is what makes a board trustworthy.
COMPUTED_PINNABLE = {"chart": "charts", "table": "tables"}

Computation = Callable[[pd.DataFrame, dict], dict]
COMPUTED_SOURCES: dict[str, tuple[Computation, str]] = {
    "drivers": (drivers_service.explain, "Drivers"),
    "significance": (statistics_service.compare, "Significance"),
    "scenarios": (scenarios_service.simulate, "Scenario"),
    "cohorts": (cohorts_service.analyze, "Retention"),
    "forecast": (forecasting_service.project, "Forecast"),
}


class BoardService:
    def __init__(self, db: Database, datasets: Any | None = None) -> None:
        self.db = db
        self.datasets = datasets

    def list(self) -> list[dict[str, Any]]:
        return self.db.list_boards()

    def create(self, title: str, description: str = "") -> dict[str, Any]:
        return self.db.create_board(title.strip() or "Untitled board", description.strip())

    def require(self, board_id: str) -> dict[str, Any]:
        board = self.db.get_board(board_id)
        if board is None:
            raise NotFoundError(f"Board '{board_id}' was not found.")
        return board

    def get(self, board_id: str) -> dict[str, Any]:
        board = self.require(board_id)
        return {**board, "items": self.db.list_board_items(board_id)}

    def update(self, board_id: str, title: str | None, description: str | None) -> dict[str, Any]:
        self.require(board_id)
        self.db.update_board(board_id, title=title.strip() if title else None,
                             description=description.strip() if description is not None else None)
        return self.get(board_id)

    def delete(self, board_id: str) -> None:
        if not self.db.delete_board(board_id):
            raise NotFoundError(f"Board '{board_id}' was not found.")

    def pin(self, board_id: str, kind: str, message_id: str | None, index: int | None,
            title: str | None = None) -> dict[str, Any]:
        self.require(board_id)
        if kind not in PINNABLE:
            raise InvalidInputError(f"'{kind}' items cannot be pinned from an analysis.")
        if not message_id or index is None:
            raise InvalidInputError("Pinning requires a message_id and an index.")
        message = self.db.get_message(message_id)
        if message is None or message["role"] != "assistant":
            raise NotFoundError("The analysis you tried to pin from no longer exists.")

        section, key = PINNABLE[kind]
        items = ((message.get("payload") or {}).get(section) or {}).get(key) or []
        if not 0 <= index < len(items):
            raise InvalidInputError("That result is not available in this analysis.")
        # Snapshot the result server-side; clients never supply pinned content. The figure
        # digest travels with it: it is what the PDF and PowerPoint exporters rebuild the
        # chart from, so stripping it would silently drop charts from every board export.
        content = dict(items[index])
        session = self.db.get_session(message["session_id"])
        return self.db.add_board_item(
            board_id,
            kind=kind,
            title=(title or content.get("title") or content.get("label") or kind.title()).strip()[:200],
            content=content,
            source_session_id=message["session_id"],
            source_question=self.db.question_before(message),
            dataset_name=session["dataset_name"] if session else None,
            wide=kind == "table",
        )

    def pin_computed(self, board_id: str, source: str, *, dataset_id: str | None,
                     params: dict[str, Any] | None, kind: str, index: int | None,
                     title: str | None = None) -> dict[str, Any]:
        """Pin a chart or table from a deterministic view, recomputed server-side.

        Drill-downs, significance tests and scenarios all reach the board this way: the
        pin is addressed by provenance (which dataset, which parameters), never by content
        the browser sent, so a pinned tile always reflects what the server would compute.
        """
        self.require(board_id)
        if self.datasets is None:
            raise InvalidInputError("Computed analysis is not available on this server.")
        if source not in COMPUTED_SOURCES:
            raise InvalidInputError(f"'{source}' results cannot be pinned.")
        if kind not in COMPUTED_PINNABLE:
            raise InvalidInputError(f"'{kind}' cannot be pinned from a {source} result.")
        if not dataset_id:
            raise InvalidInputError(f"Pinning a {source} result requires a dataset_id.")

        compute, fallback_title = COMPUTED_SOURCES[source]
        record = self.datasets.get(dataset_id)
        result = compute(self.datasets.load_frame(dataset_id), record["profile"], **(params or {}))
        items = result[COMPUTED_PINNABLE[kind]]
        position = index or 0
        if not 0 <= position < len(items):
            raise InvalidInputError(f"That {source} result is not available.")
        content = items[position]
        headline = result.get("headline") or (result.get("verdict") or {}).get("headline") or ""
        return self.db.add_board_item(
            board_id,
            kind=kind,
            title=(title or content.get("title") or fallback_title).strip()[:200],
            content=content,
            source_question=headline,
            dataset_name=record["name"],
            wide=kind == "table",
        )

    def pin_drivers(self, board_id: str, **kwargs: Any) -> dict[str, Any]:
        """Backwards-compatible alias for the original drill-down pin path."""
        return self.pin_computed(board_id, "drivers", **kwargs)

    def add_note(self, board_id: str, text: str | None, title: str | None = None) -> dict[str, Any]:
        self.require(board_id)
        if not text or not text.strip():
            raise InvalidInputError("A note needs some text.")
        return self.db.add_board_item(board_id, kind="note", title=(title or "Note").strip()[:200],
                                      content={"text": text.strip()})

    def update_item(self, board_id: str, item_id: str, *, title: str | None, wide: bool | None,
                    text: str | None) -> dict[str, Any]:
        item = self._require_item(board_id, item_id)
        content = None
        if text is not None:
            if item["kind"] != "note":
                raise InvalidInputError("Only notes have editable text.")
            content = {"text": text.strip()}
        self.db.update_board_item(board_id, item_id, title=title.strip() if title else None, wide=wide,
                                  content=content)
        return self._require_item(board_id, item_id)

    def remove_item(self, board_id: str, item_id: str) -> None:
        self._require_item(board_id, item_id)
        self.db.delete_board_item(board_id, item_id)

    def reorder(self, board_id: str, item_ids: list[str]) -> dict[str, Any]:
        self.require(board_id)
        existing = {item["id"] for item in self.db.list_board_items(board_id)}
        if set(item_ids) != existing or len(item_ids) != len(existing):
            raise InvalidInputError("The new order must list every item on the board exactly once.")
        self.db.reorder_board_items(board_id, item_ids)
        return self.get(board_id)

    def _require_item(self, board_id: str, item_id: str) -> dict[str, Any]:
        item = self.db.get_board_item(board_id, item_id)
        if item is None:
            raise NotFoundError(f"Item '{item_id}' was not found on this board.")
        return item

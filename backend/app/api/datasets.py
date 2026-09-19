from __future__ import annotations

from typing import Any, Literal

import re

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import (
    get_activity,
    get_datasets,
    get_monitors,
    get_notifier,
    get_settings_dep,
)
from app.core.auth import actor
from app.core.config import Settings
from app.core.errors import InvalidInputError, NotFoundError
from app.services import cohorts as cohort_service
from app.services import contracts as contract_service
from app.services import drivers as drivers_service
from app.services import forecasting as forecast_service
from app.services import privacy as privacy_service
from app.services import scenarios as scenario_service
from app.services import statistics as statistics_service
from app.services.activity import ActivityService
from app.services.datasets import DatasetService
from app.services.monitors import MonitorService
from app.services.notifications import NotificationService

router = APIRouter(prefix="/datasets", tags=["datasets"])


class DriversBody(BaseModel):
    """Everything is optional: omitted fields fall back to the profile's best guess."""

    measure: str | None = None
    date_column: str | None = None
    dimensions: list[str] | None = None
    # Look through one dimension by name; otherwise the best-scoring one leads.
    focus: str | None = None
    aggregation: Literal["sum", "mean"] | None = None
    period: Literal["auto", "yoy", "month", "week", "halves", "custom"] = "auto"
    baseline_start: str | None = None
    baseline_end: str | None = None
    current_start: str | None = None
    current_end: str | None = None
    top_n: int = Field(default=8, ge=2, le=30)


class SignificanceBody(BaseModel):
    """Everything is optional: omitted fields fall back to the profile's best guess."""

    measure: str | None = None
    mode: Literal["segments", "periods"] = "segments"
    dimension: str | None = None
    group_a: str | None = None
    group_b: str | None = None
    date_column: str | None = None
    period: Literal["auto", "yoy", "month", "week", "halves", "custom"] = "auto"
    baseline_start: str | None = None
    baseline_end: str | None = None
    current_start: str | None = None
    current_end: str | None = None
    alpha: float = Field(default=0.05, gt=0.0001, le=0.2)
    scan: bool = True


class SegmentLever(BaseModel):
    """Fractions, not percentages: 0.1 is +10%, and share_points is a share of the whole."""

    volume_pct: float | None = None
    rate_pct: float | None = None
    share_points: float | None = None


class Levers(BaseModel):
    global_: SegmentLever = Field(default_factory=SegmentLever, alias="global")
    segments: dict[str, SegmentLever] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}

    def to_payload(self) -> dict[str, Any]:
        return {
            "global": self.global_.model_dump(exclude_none=True),
            "segments": {k: v.model_dump(exclude_none=True) for k, v in self.segments.items()},
        }


class ScenarioBody(BaseModel):
    measure: str | None = None
    dimension: str | None = None
    date_column: str | None = None
    aggregation: Literal["sum", "mean"] | None = None
    period: Literal["auto", "yoy", "month", "week", "halves", "custom"] = "auto"
    baseline_start: str | None = None
    baseline_end: str | None = None
    current_start: str | None = None
    current_end: str | None = None
    levers: Levers = Field(default_factory=Levers)

    def to_kwargs(self) -> dict[str, Any]:
        payload = self.model_dump(exclude={"levers"})
        payload["levers"] = self.levers.to_payload()
        return payload


class CohortBody(BaseModel):
    """Everything is optional: omitted fields fall back to the profile's best guess."""

    entity: str | None = None
    date_column: str | None = None
    measure: str | None = None
    granularity: Literal["auto", "day", "week", "month", "quarter"] = "auto"
    periods: int = Field(default=12, ge=1, le=24)
    min_cohort_size: int = Field(default=3, ge=1, le=1000)


class ForecastBody(BaseModel):
    """Everything is optional: omitted fields fall back to the profile's best guess."""

    measure: str | None = None
    date_column: str | None = None
    aggregation: Literal["sum", "mean"] | None = None
    granularity: Literal["auto", "day", "week", "month", "quarter"] = "auto"
    horizon: int = Field(default=6, ge=1, le=36)
    # "auto" picks whichever candidate wins the walk-forward backtest.
    method: str = "auto"
    interval: float = Field(default=0.8, ge=0.5, le=0.99)


class PrivacyBody(BaseModel):
    """Column name → what should happen to it: keep, mask, hash or drop."""

    policy: dict[str, Literal["keep", "mask", "hash", "drop"]] = Field(default_factory=dict)


class GoalSeekBody(ScenarioBody):
    target: float
    lever: Literal["rate", "volume"] = "rate"
    segment: str | None = None

    def to_kwargs(self) -> dict[str, Any]:
        payload = self.model_dump(exclude={"levers", "target", "lever", "segment"})
        payload["levers"] = self.levers.to_payload()
        return payload


@router.get("")
def list_datasets(datasets: DatasetService = Depends(get_datasets)) -> list[dict[str, Any]]:
    return datasets.db.list_datasets()


@router.post("", status_code=201)
def upload_dataset(
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    sheet: str | None = Form(default=None),
    replaces: str | None = Form(default=None),
    datasets: DatasetService = Depends(get_datasets),
    monitors: MonitorService = Depends(get_monitors),
    notifier: NotificationService = Depends(get_notifier),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    """Upload a dataset. Pass `replaces` to file it as a new version of an existing one."""
    if not file.filename:
        raise InvalidInputError("The upload is missing a file name.")
    dataset = datasets.ingest_stream(file.filename, file.file, sheet=sheet or None,
                                     replaces=replaces or None)
    activity.record(
        "dataset.version" if replaces else "dataset.upload",
        actor=actor(request), subject_kind="dataset", subject_id=dataset["id"],
        subject_title=f"{dataset['name']} v{dataset.get('version', 1)}",
        detail=f"{dataset['n_rows']:,} rows × {dataset['n_cols']} columns",
    )
    if replaces:
        # Fresh data is exactly when a watched metric should be re-checked.
        background.add_task(monitors.run_for_lineage, dataset["id"])
    if dataset.get("contract_result"):
        # A contract that broke on arrival is news, not something to find days later.
        background.add_task(_announce_contract, notifier, dataset)
    return dataset


def _announce_contract(notifier: NotificationService, dataset: dict[str, Any]) -> None:
    alert = notifier.contract_alert(dataset, dataset.get("contract_result") or {})
    if alert is not None:
        notifier.notify(alert)


@router.post("/sample", status_code=201)
def load_sample_dataset(
    datasets: DatasetService = Depends(get_datasets),
    settings: Settings = Depends(get_settings_dep),
) -> dict[str, Any]:
    path = settings.sample_data_path
    if not path.exists():
        raise NotFoundError("The sample dataset is not installed. Run scripts/generate_sample_data.py.")
    return datasets.ingest_path(path, path.name, name="Retail Sales (Sample)")


@router.get("/{dataset_id}")
def get_dataset(dataset_id: str, datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    return datasets.get(dataset_id)


@router.get("/{dataset_id}/preview")
def preview_dataset(
    dataset_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    return datasets.preview(dataset_id, offset=offset, limit=limit)


@router.get("/{dataset_id}/versions")
def dataset_versions(dataset_id: str,
                     datasets: DatasetService = Depends(get_datasets)) -> list[dict[str, Any]]:
    """Every upload in this dataset's lineage, oldest first, each with its diff."""
    return datasets.versions(dataset_id)


@router.get("/{dataset_id}/semantics")
def get_semantics(dataset_id: str,
                  datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    return datasets.get_semantics(dataset_id)


@router.put("/{dataset_id}/semantics")
def put_semantics(
    dataset_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Replace the binding metric definitions, rules and glossary for this dataset."""
    return datasets.set_semantics(dataset_id, body)


@router.get("/{dataset_id}/contract")
def get_contract(dataset_id: str,
                 datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """The dataset's expectations and the last time they were checked."""
    return datasets.get_contract(dataset_id)


@router.put("/{dataset_id}/contract")
def put_contract(
    dataset_id: str,
    body: dict[str, Any] = Body(default_factory=dict),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Replace the expectations; they are checked against this version straight away."""
    return datasets.set_contract(dataset_id, body)


@router.post("/{dataset_id}/contract/suggest")
def suggest_contract(dataset_id: str,
                     datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Expectations inferred from the profile. Returned for review — not saved."""
    return datasets.suggest_contract(dataset_id)


@router.post("/{dataset_id}/contract/check")
def check_contract(dataset_id: str,
                   datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    return datasets.check_contract(dataset_id)


@router.get("/{dataset_id}/contract.md", response_class=PlainTextResponse)
def contract_markdown(dataset_id: str,
                      datasets: DatasetService = Depends(get_datasets)) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    state = datasets.get_contract(dataset_id)
    return PlainTextResponse(
        contract_service.contract_markdown(state["contract"], state["result"], record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="data-contract.md"'},
    )


@router.get("/{dataset_id}/drivers/options")
def driver_options(dataset_id: str,
                   datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Which measures, dimensions and date columns a drill-down can use."""
    return drivers_service.driver_options(datasets.get(dataset_id)["profile"])


@router.post("/{dataset_id}/drivers")
def explain_drivers(
    dataset_id: str,
    body: DriversBody = Body(default_factory=DriversBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Decompose the change in a measure into contributions, mix and rate. No model call."""
    record = datasets.get(dataset_id)
    return drivers_service.explain(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )


@router.post("/{dataset_id}/drivers/export.md", response_class=PlainTextResponse)
def export_drivers(
    dataset_id: str,
    body: DriversBody = Body(default_factory=DriversBody),
    datasets: DatasetService = Depends(get_datasets),
) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    result = drivers_service.explain(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )
    return PlainTextResponse(
        drivers_service.explain_markdown(result, record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="driver-analysis.md"'},
    )


@router.get("/{dataset_id}/significance/options")
def significance_options(dataset_id: str,
                         datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Which measures, segments and dates a comparison can be built from."""
    return statistics_service.significance_options(datasets.get(dataset_id)["profile"])


@router.post("/{dataset_id}/significance")
def test_significance(
    dataset_id: str,
    body: SignificanceBody = Body(default_factory=SignificanceBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Is the difference between two groups real, or is it noise? No model call."""
    record = datasets.get(dataset_id)
    return statistics_service.compare(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )


@router.post("/{dataset_id}/significance/export.md", response_class=PlainTextResponse)
def export_significance(
    dataset_id: str,
    body: SignificanceBody = Body(default_factory=SignificanceBody),
    datasets: DatasetService = Depends(get_datasets),
) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    result = statistics_service.compare(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )
    return PlainTextResponse(
        statistics_service.compare_markdown(result, record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="significance-test.md"'},
    )


@router.get("/{dataset_id}/scenarios/options")
def scenario_options(dataset_id: str,
                     datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Which measures, segmentations and periods a scenario can be built on."""
    return scenario_service.scenario_options(datasets.get(dataset_id)["profile"])


@router.post("/{dataset_id}/scenarios")
def simulate_scenario(
    dataset_id: str,
    body: ScenarioBody = Body(default_factory=ScenarioBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Project the measure under the given levers. No model call."""
    record = datasets.get(dataset_id)
    return scenario_service.simulate(
        datasets.load_frame(dataset_id), record["profile"], **body.to_kwargs()
    )


@router.post("/{dataset_id}/scenarios/goal-seek")
def seek_goal(
    dataset_id: str,
    body: GoalSeekBody = Body(...),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Solve for the lever value that reaches a target — or report that none does."""
    record = datasets.get(dataset_id)
    return scenario_service.goal_seek(
        datasets.load_frame(dataset_id), record["profile"],
        target=body.target, lever=body.lever, segment=body.segment, **body.to_kwargs(),
    )


@router.post("/{dataset_id}/scenarios/export.md", response_class=PlainTextResponse)
def export_scenario(
    dataset_id: str,
    body: ScenarioBody = Body(default_factory=ScenarioBody),
    datasets: DatasetService = Depends(get_datasets),
) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    result = scenario_service.simulate(
        datasets.load_frame(dataset_id), record["profile"], **body.to_kwargs()
    )
    return PlainTextResponse(
        scenario_service.simulate_markdown(result, record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="scenario.md"'},
    )


@router.get("/{dataset_id}/cohorts/options")
def cohort_options(dataset_id: str,
                   datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Which entity, date and value columns a cohort grid can be built from."""
    return cohort_service.cohort_options(datasets.get(dataset_id)["profile"])


@router.post("/{dataset_id}/cohorts")
def analyze_cohorts(
    dataset_id: str,
    body: CohortBody = Body(default_factory=CohortBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Retention by cohort, respecting right-censoring. No model call."""
    record = datasets.get(dataset_id)
    return cohort_service.analyze(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )


@router.post("/{dataset_id}/cohorts/export.md", response_class=PlainTextResponse)
def export_cohorts(
    dataset_id: str,
    body: CohortBody = Body(default_factory=CohortBody),
    datasets: DatasetService = Depends(get_datasets),
) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    result = cohort_service.analyze(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )
    return PlainTextResponse(
        cohort_service.cohort_markdown(result, record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cohort-retention.md"'},
    )


@router.get("/{dataset_id}/forecast/options")
def forecast_options(dataset_id: str,
                     datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Which measures, dates, grains and methods a projection can be built on."""
    return forecast_service.forecast_options(datasets.get(dataset_id)["profile"])


@router.post("/{dataset_id}/forecast")
def forecast(
    dataset_id: str,
    body: ForecastBody = Body(default_factory=ForecastBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Project a measure forward, with the walk-forward backtest that chose the method."""
    record = datasets.get(dataset_id)
    return forecast_service.project(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )


@router.post("/{dataset_id}/forecast/export.md", response_class=PlainTextResponse)
def export_forecast(
    dataset_id: str,
    body: ForecastBody = Body(default_factory=ForecastBody),
    datasets: DatasetService = Depends(get_datasets),
) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    result = forecast_service.project(
        datasets.load_frame(dataset_id), record["profile"], **body.model_dump()
    )
    return PlainTextResponse(
        forecast_service.project_markdown(result, record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="forecast.md"'},
    )


@router.get("/{dataset_id}/privacy")
def get_privacy(dataset_id: str,
                datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """What personal data was detected, and what the policy says to do about it."""
    return datasets.get_privacy(dataset_id)


@router.post("/{dataset_id}/privacy/scan")
def scan_privacy(dataset_id: str,
                 datasets: DatasetService = Depends(get_datasets)) -> dict[str, Any]:
    """Re-run detection against the table as it stands now."""
    return datasets.scan_privacy(dataset_id)


@router.put("/{dataset_id}/privacy")
def put_privacy(
    dataset_id: str,
    body: PrivacyBody = Body(default_factory=PrivacyBody),
    datasets: DatasetService = Depends(get_datasets),
) -> dict[str, Any]:
    """Record what should happen to each column. Nothing is rewritten until /apply."""
    return datasets.set_privacy(dataset_id, body.policy)


@router.post("/{dataset_id}/privacy/apply")
def apply_privacy(
    dataset_id: str,
    request: Request,
    datasets: DatasetService = Depends(get_datasets),
    activity: ActivityService = Depends(get_activity),
) -> dict[str, Any]:
    """Rewrite the cleaned table under the policy. Irreversible, and audited."""
    record = datasets.get(dataset_id)
    state = datasets.apply_privacy(dataset_id)
    applied = state["state"].get("applied") or []
    activity.record(
        "dataset.redact", actor=actor(request), subject_kind="dataset", subject_id=dataset_id,
        subject_title=record["name"],
        detail=", ".join(f"{a['column']} → {a['action']}" for a in applied[:8]) or "no columns",
    )
    return state


@router.get("/{dataset_id}/privacy.md", response_class=PlainTextResponse)
def privacy_markdown(dataset_id: str,
                     datasets: DatasetService = Depends(get_datasets)) -> PlainTextResponse:
    record = datasets.get(dataset_id)
    state = datasets.get_privacy(dataset_id)
    return PlainTextResponse(
        privacy_service.privacy_markdown(state["scan"], state["state"], record["name"]),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="privacy-review.md"'},
    )


@router.get("/{dataset_id}/download")
def download_dataset(
    dataset_id: str,
    format: str = Query(default="csv", pattern="^(csv|parquet)$"),
    datasets: DatasetService = Depends(get_datasets),
) -> Response:
    """The cleaned table, so an exported notebook reproduces the analysis exactly."""
    record = datasets.get(dataset_id)
    payload, media_type, extension = datasets.export_bytes(dataset_id, format)
    slug = re.sub(r"[^a-z0-9]+", "-", record["name"].lower()).strip("-")[:50] or "dataset"
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{slug}-clean.{extension}"'},
    )


@router.delete("/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: str,
    request: Request,
    datasets: DatasetService = Depends(get_datasets),
    activity: ActivityService = Depends(get_activity),
) -> Response:
    record = datasets.get(dataset_id)
    datasets.delete(dataset_id)
    activity.record("dataset.delete", actor=actor(request), subject_kind="dataset",
                    subject_id=dataset_id, subject_title=record["name"])
    return Response(status_code=204)

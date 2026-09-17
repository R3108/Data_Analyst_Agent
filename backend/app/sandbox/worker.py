"""Sandbox worker — executes one analysis snippet inside an isolated child process.

Protocol: the parent writes a JSON job to stdin; the worker writes a single JSON
result to stdout, prefixed by RESULT_MARKER. This file is launched with
`python -I worker.py` and must NOT import anything from the `app` package.

Runtime defences (layer 2 & 3, after the static AST policy):
  * restricted builtins and an import guard for the user code
  * a `sys.addaudithook` hook that blocks process spawning, networking, file
    writes, file reads outside the Python installation, ctypes and more
  * POSIX resource limits (CPU time, file size, no forking) where available
The parent process additionally enforces a wall-clock timeout and a memory cap.
"""

from __future__ import annotations

import builtins
import datetime as _dt
import io
import json
import math
import os
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal

RESULT_MARKER = "<<<NUMERA_SANDBOX_RESULT>>>"
MAX_STDOUT_CHARS = 20_000
MAX_CHARTS = 6
MAX_TABLES = 8
MAX_KPIS = 16
MAX_TABLE_ROWS = 200
MAX_FIGURE_CHARS = 5_000_000
DIGEST_POINTS = 40

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQUENTIAL = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
DIVERGING = ["#2a78d6", "#86b6ef", "#f0efec", "#ee9a9a", "#e34948"]
KPI_FORMATS = {"auto", "number", "integer", "currency", "percent", "text"}

BLOCKED_BUILTINS = (
    "open", "eval", "exec", "compile", "input", "__import__", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "help", "memoryview",
)
BLOCKED_EVENT_PREFIXES = (
    "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty",
    "os.startfile", "os.kill", "os.killpg", "os.remove", "os.unlink", "os.rename", "os.replace",
    "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.link", "os.symlink", "os.truncate",
    "os.putenv", "os.unsetenv", "os.chdir", "shutil.", "socket.", "ctypes.", "winreg.", "_winapi.",
    "msvcrt.", "urllib.", "http.", "ftplib.", "smtplib.", "telnetlib.", "webbrowser.", "sqlite3.",
    "pty.", "fcntl.", "pickle.find_class", "cpython.run_", "sys.addaudithook",
    "sys.settrace", "sys.setprofile", "code.__new__", "function.__new__", "resource.setrlimit",
)
DANGEROUS_MODULE_ROOTS = frozenset({
    "subprocess", "socket", "ssl", "ctypes", "_ctypes", "multiprocessing", "urllib", "http",
    "ftplib", "smtplib", "telnetlib", "webbrowser", "asyncio", "pty", "shutil", "tempfile",
    "sqlite3", "winreg", "_winapi", "msvcrt", "requests", "httpx", "pdb", "code", "codeop",
})


# --------------------------------------------------------------------------------------
# JSON helpers (duplicated from app.core.serialization on purpose: no app imports here)
# --------------------------------------------------------------------------------------


def _jsonable(value):
    import numpy as np
    import pandas as pd

    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        f = float(value)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(value, str):
        return value
    if isinstance(value, Decimal):
        return _jsonable(float(value))
    if isinstance(value, (pd.Timestamp, _dt.datetime, _dt.date)):
        return None if value is pd.NaT else value.isoformat()
    try:
        if pd.isna(value) is True:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (pd.Timedelta, _dt.timedelta)):
        return str(value)
    if isinstance(value, pd.Period):
        return str(value)
    if isinstance(value, pd.Interval):
        return str(value)
    return str(value)


def _column_kind(series) -> str:
    import pandas as pd

    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    return "text"


class _BoundedIO(io.StringIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.truncated = False

    def write(self, s: str) -> int:
        remaining = self.limit - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(s)
        if len(s) > remaining:
            self.truncated = True
            super().write(s[:remaining])
            return len(s)
        return super().write(s)


# --------------------------------------------------------------------------------------
# Output collector — the helper API exposed to generated code
# --------------------------------------------------------------------------------------


class Collector:
    def __init__(self, pd, go, pio) -> None:
        self.pd, self.go, self.pio = pd, go, pio
        self.kpis: list[dict] = []
        self.charts: list[dict] = []
        self.tables: list[dict] = []
        self.warnings: list[str] = []
        self._figure_ids: set[int] = set()

    # kpi(label, value, format="auto", delta=None, delta_label=None, higher_is_better=True, description=None)
    def kpi(self, label, value, format="auto", delta=None, delta_label=None,
            higher_is_better=True, description=None):
        if len(self.kpis) >= MAX_KPIS:
            self.warnings.append(f"KPI limit reached; '{label}' was ignored.")
            return
        if format not in KPI_FORMATS:
            format = "auto"
        if hasattr(value, "item") and not isinstance(value, str):
            try:
                value = value.item()
            except (ValueError, AttributeError):
                pass
        self.kpis.append({
            "label": str(label),
            "value": _jsonable(value),
            "format": format,
            "delta": _jsonable(delta) if delta is not None else None,
            "delta_label": str(delta_label) if delta_label is not None else None,
            "higher_is_better": bool(higher_is_better),
            "description": str(description) if description else None,
        })

    def chart(self, fig, title=None, caption=None):
        if not isinstance(fig, self.go.Figure):
            raise TypeError("chart() expects a plotly Figure (from plotly.express or plotly.graph_objects)")
        if len(self.charts) >= MAX_CHARTS:
            self.warnings.append("Chart limit reached; extra charts were ignored.")
            return
        self._figure_ids.add(id(fig))
        layout_title = fig.layout.title.text if fig.layout.title and fig.layout.title.text else None
        title = str(title or layout_title or f"Chart {len(self.charts) + 1}")
        fig.update_layout(title=None)
        spec = self.pio.to_json(fig, validate=False, pretty=False)
        if len(spec) > MAX_FIGURE_CHARS:
            raise ValueError(
                f"Figure '{title}' is too large to render ({len(spec):,} chars). "
                "Aggregate or sample the data before plotting."
            )
        self.charts.append({
            "title": title,
            "caption": str(caption) if caption else None,
            "figure": json.loads(spec),
            "digest": _figure_digest(fig),
        })

    def table(self, data, title=None, max_rows=50):
        pd = self.pd
        if len(self.tables) >= MAX_TABLES:
            self.warnings.append("Table limit reached; extra tables were ignored.")
            return
        if isinstance(data, pd.Series):
            frame = data.to_frame(name=data.name if data.name is not None else "value")
        elif isinstance(data, pd.DataFrame):
            frame = data
        else:
            frame = pd.DataFrame(data)
        if isinstance(frame.columns, pd.MultiIndex):
            frame = frame.copy()
            frame.columns = [" / ".join(str(p) for p in col if str(p)) for col in frame.columns]
        index = frame.index
        if isinstance(index, pd.MultiIndex) or index.name is not None:
            frame = frame.reset_index()
        elif not pd.api.types.is_integer_dtype(index):
            frame = frame.reset_index()  # meaningful unnamed labels, e.g. describe()
        else:
            frame = frame.reset_index(drop=True)  # positional index left over from sorting/filtering
        frame.columns = [str(c) for c in frame.columns]
        max_rows = max(1, min(int(max_rows), MAX_TABLE_ROWS))
        view = frame.head(max_rows)
        self.tables.append({
            "title": str(title or f"Table {len(self.tables) + 1}"),
            "columns": [{"name": c, "kind": _column_kind(view[c])} for c in view.columns],
            "rows": [[_jsonable(v) for v in row] for row in view.itertuples(index=False, name=None)],
            "total_rows": int(len(frame)),
            "truncated": bool(len(frame) > max_rows),
        })


def _figure_digest(fig) -> dict:
    """A compact, numeric summary of the figure so the report writer can reason about it."""
    traces = []
    for trace in list(fig.data)[:8]:
        entry = {"type": trace.type, "name": _safe_prop(trace, "name")}
        for key in ("x", "y", "labels", "values", "z"):
            value = _safe_prop(trace, key)
            if value is None or isinstance(value, str):
                continue
            try:
                seq = list(value)
            except TypeError:
                continue
            entry[f"{key}_count"] = len(seq)
            entry[key] = [_jsonable(v) for v in seq[:DIGEST_POINTS]]
        traces.append(entry)
    axes = {}
    for axis in ("xaxis", "yaxis"):
        axis_obj = _safe_prop(fig.layout, axis)
        title = _safe_prop(_safe_prop(axis_obj, "title"), "text") if axis_obj is not None else None
        if title:
            axes[axis] = title
    return {"traces": traces, "axes": axes}


def _safe_prop(obj, name):
    if obj is None:
        return None
    try:
        return obj[name]
    except Exception:
        return None


# --------------------------------------------------------------------------------------
# Forecasting helpers
# --------------------------------------------------------------------------------------


def _default_season_length(freq) -> int | None:
    if not freq:
        return None
    code = str(freq).upper().lstrip("0123456789")
    if code.startswith("MIN"):
        return None
    if code.startswith(("MS", "ME", "M")):
        return 12
    if code.startswith(("QS", "QE", "Q")):
        return 4
    if code.startswith("W"):
        return 52
    if code.startswith(("D", "B")):
        return 7
    if code.startswith("H"):
        return 24
    return None


def make_forecast(pd, np):
    def forecast(series, periods=6, season_length=None):
        """Additive trend + seasonality projection with an approximate 95% prediction interval.

        Returns a DataFrame with columns: period, actual, fitted, forecast, lower, upper.
        """
        if isinstance(series, pd.DataFrame):
            if series.shape[1] != 1:
                raise ValueError("forecast() expects a Series indexed by date (or a one-column DataFrame)")
            series = series.iloc[:, 0]
        s = pd.to_numeric(pd.Series(series), errors="coerce").dropna().astype("float64")
        if len(s) < 6:
            raise ValueError(f"forecast() needs at least 6 observations, got {len(s)}")
        periods = int(max(1, min(int(periods), 60)))

        freq = None
        if isinstance(s.index, pd.DatetimeIndex):
            s = s.sort_index()
            freq = s.index.freqstr or pd.infer_freq(s.index)
            if freq is None:
                raise ValueError(
                    "forecast() needs evenly spaced dates. Aggregate first, e.g. "
                    "df.set_index('date')['value'].resample('MS').sum()"
                )
        if season_length is None:
            season_length = _default_season_length(freq)

        y = s.to_numpy()
        n = y.size
        t = np.arange(n, dtype=float)
        pattern = None
        if season_length and season_length >= 2 and n >= 2 * season_length:
            smooth = pd.Series(y).rolling(season_length, center=True, min_periods=season_length).mean().to_numpy()
            detrended = y - smooth
            pattern = np.array([
                np.nanmean(detrended[i::season_length]) if np.isfinite(detrended[i::season_length]).any() else 0.0
                for i in range(season_length)
            ])
            pattern = np.nan_to_num(pattern - np.nanmean(pattern))
        seasonal = pattern[np.arange(n) % season_length] if pattern is not None else np.zeros(n)

        slope, intercept = np.polyfit(t, y - seasonal, 1)
        fitted = intercept + slope * t + seasonal
        dof = max(n - 2 - ((season_length - 1) if pattern is not None else 0), 1)
        sigma = float(np.sqrt(np.sum((y - fitted) ** 2) / dof))

        steps = np.arange(1, periods + 1)
        future_t = (n - 1 + steps).astype(float)
        future_seasonal = pattern[(n - 1 + steps) % season_length] if pattern is not None else 0.0
        point = intercept + slope * future_t + future_seasonal
        leverage = 1 + 1 / n + (future_t - t.mean()) ** 2 / np.sum((t - t.mean()) ** 2)
        half_width = 1.96 * sigma * np.sqrt(leverage)
        lower, upper = point - half_width, point + half_width
        if (y >= 0).all():
            point, lower = np.maximum(point, 0), np.maximum(lower, 0)

        if freq is not None:
            future_index = list(pd.date_range(s.index[-1], periods=periods + 1, freq=freq)[1:])
        else:
            future_index = list(range(n, n + periods))

        history = pd.DataFrame({"period": list(s.index), "actual": y, "fitted": fitted,
                                "forecast": np.nan, "lower": np.nan, "upper": np.nan})
        future = pd.DataFrame({"period": future_index, "actual": np.nan, "fitted": np.nan,
                               "forecast": point, "lower": lower, "upper": upper})
        return pd.concat([history, future], ignore_index=True)

    return forecast


def make_chart_forecast(pd, go, collector):
    def chart_forecast(frame, title="Forecast", caption=None, y_label="Value", x_label="Period"):
        required = {"period", "actual", "forecast", "lower", "upper"}
        if not isinstance(frame, pd.DataFrame) or not required <= set(frame.columns):
            raise TypeError("chart_forecast() expects the DataFrame returned by forecast()")
        history = frame[frame["actual"].notna()]
        future = frame[frame["forecast"].notna()]
        if history.empty or future.empty:
            raise ValueError("chart_forecast() needs both history and forecast rows")
        anchor_x, anchor_y = history["period"].iloc[-1], float(history["actual"].iloc[-1])
        xs = [anchor_x, *future["period"].tolist()]
        upper = [anchor_y, *future["upper"].tolist()]
        lower = [anchor_y, *future["lower"].tolist()]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=xs + xs[::-1], y=upper + lower[::-1], fill="toself",
                                 fillcolor="rgba(42,120,214,0.14)", line=dict(width=0),
                                 hoverinfo="skip", name="95% interval"))
        fig.add_trace(go.Scatter(x=history["period"].tolist(), y=history["actual"].tolist(), mode="lines",
                                 name="Actual", line=dict(color=PALETTE[0], width=2)))
        fig.add_trace(go.Scatter(x=xs, y=[anchor_y, *future["forecast"].tolist()], mode="lines+markers",
                                 name="Forecast", line=dict(color=PALETTE[0], width=2, dash="dash"),
                                 marker=dict(size=7)))
        fig.update_layout(xaxis_title=x_label, yaxis_title=y_label, hovermode="x unified")
        collector.chart(fig, title=title, caption=caption)

    return chart_forecast


# --------------------------------------------------------------------------------------
# Environment & defences
# --------------------------------------------------------------------------------------


def _apply_resource_limits(cpu_seconds: int | None) -> None:
    try:
        import resource
    except ImportError:  # Windows
        return
    limits = [(resource.RLIMIT_FSIZE, 1024 * 1024)]
    if cpu_seconds:
        limits.append((resource.RLIMIT_CPU, int(cpu_seconds)))
    if hasattr(resource, "RLIMIT_NPROC"):
        limits.append((resource.RLIMIT_NPROC, 0))
    for limit, value in limits:
        try:
            resource.setrlimit(limit, (value, value))
        except (ValueError, OSError):
            pass


def _prepare_environment(job: dict) -> dict:
    import warnings

    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import collections, calendar, fractions, functools, itertools, re, statistics, string, textwrap, typing  # noqa: E401,F401
    import numpy as np
    import pandas as pd
    import plotly
    import plotly.colors  # noqa: F401
    import plotly.express as px
    import plotly.graph_objects as go
    import plotly.io as pio
    import plotly.subplots  # noqa: F401

    template = go.layout.Template(pio.templates["plotly_white"])
    template.layout.update(
        colorway=PALETTE,
        font=dict(family="Inter, system-ui, -apple-system, Segoe UI, sans-serif", size=12, color="#52514e"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=56, r=20, t=36, b=48),
        bargap=0.35,
        barcornerradius=4,
        hoverlabel=dict(bgcolor="#ffffff", bordercolor="#e1e0d9", font=dict(color="#0b0b0b")),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title=dict(text="")),
        xaxis=dict(showgrid=False, linecolor="#c3c2b7", ticks="", zeroline=False, automargin=True),
        yaxis=dict(gridcolor="#e1e0d9", linecolor="#c3c2b7", zeroline=False, ticks="", automargin=True),
        colorscale=dict(
            sequential=[[i / (len(SEQUENTIAL) - 1), c] for i, c in enumerate(SEQUENTIAL)],
            diverging=[[i / (len(DIVERGING) - 1), c] for i, c in enumerate(DIVERGING)],
        ),
    )
    template.data.scatter = [go.Scatter(line=dict(width=2), marker=dict(size=8))]
    pio.templates["numera"] = template
    pio.templates.default = "numera"
    px.defaults.template = "numera"
    px.defaults.color_discrete_sequence = PALETTE
    px.defaults.color_continuous_scale = SEQUENTIAL

    pd.set_option("display.width", 160)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.max_rows", 60)

    df = pd.read_parquet(job["data_path"])
    return {"pd": pd, "np": np, "px": px, "go": go, "pio": pio, "plotly": plotly, "df": df}


def _read_roots() -> list[str]:
    import site

    roots = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    try:
        roots.update(site.getsitepackages())
        roots.add(site.getusersitepackages())
    except AttributeError:
        pass
    for name in ("numpy", "pandas", "plotly", "pyarrow", "dateutil", "pytz", "tzdata", "narwhals"):
        module = sys.modules.get(name)
        if module is not None and getattr(module, "__file__", None):
            roots.add(os.path.dirname(os.path.dirname(module.__file__)))
    roots.update({"/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo"})
    return sorted(os.path.normcase(os.path.abspath(r)) for r in roots if r)


def _install_audit_hook(read_roots: list[str]) -> None:
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def path_allowed(path) -> bool:
        if isinstance(path, int):
            return True
        if isinstance(path, bytes):
            path = path.decode("utf-8", "replace")
        try:
            candidate = os.path.normcase(os.path.abspath(os.fspath(path)))
        except TypeError:
            return False
        return any(candidate == root or candidate.startswith(root + os.sep) for root in read_roots)

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
                raise PermissionError("Writing files is not allowed in the analysis sandbox")
            if isinstance(flags, int) and flags & write_flags:
                raise PermissionError("Writing files is not allowed in the analysis sandbox")
            if not path_allowed(path):
                raise PermissionError("Reading files is not allowed in the analysis sandbox")
            return
        if event in ("os.listdir", "os.scandir", "glob.glob"):
            if args and not path_allowed(args[0] if args[0] is not None else "."):
                raise PermissionError("Listing directories is not allowed in the analysis sandbox")
            return
        if event == "import":
            module = args[0] if args else ""
            if isinstance(module, str) and module.split(".")[0] in DANGEROUS_MODULE_ROOTS:
                raise ImportError(f"Module '{module}' is blocked in the analysis sandbox")
            return
        if event.startswith(BLOCKED_EVENT_PREFIXES):
            raise PermissionError(f"Operation '{event}' is not allowed in the analysis sandbox")

    sys.addaudithook(hook)


def _build_globals(env: dict, collector: Collector, runtime_roots: set[str]) -> dict:
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            raise ImportError("Relative imports are not allowed")
        if name.split(".")[0] not in runtime_roots:
            raise ImportError(f"Import of '{name}' is not allowed in the analysis sandbox")
        return real_import(name, globals, locals, fromlist, level)

    safe_builtins = {k: v for k, v in builtins.__dict__.items() if k not in BLOCKED_BUILTINS}
    safe_builtins["__import__"] = guarded_import

    return {
        "__builtins__": safe_builtins,
        "__name__": "__analysis__",
        "df": env["df"],
        "pd": env["pd"],
        "np": env["np"],
        "px": env["px"],
        "go": env["go"],
        "kpi": collector.kpi,
        "chart": collector.chart,
        "table": collector.table,
        "forecast": make_forecast(env["pd"], env["np"]),
        "chart_forecast": make_chart_forecast(env["pd"], env["go"], collector),
    }


def _format_exception(exc: BaseException, code: str, df_columns: list[str]) -> tuple[str, int | None]:
    tb = traceback.extract_tb(exc.__traceback__)
    user_frames = [f for f in tb if f.filename == "<analysis>"]
    line_no = user_frames[-1].lineno if user_frames else None
    message = f"{type(exc).__name__}: {exc}"
    if line_no:
        lines = code.splitlines()
        source = lines[line_no - 1].strip() if 0 < line_no <= len(lines) else ""
        message += f"\n  at line {line_no}: {source}"
    library_frames = [f for f in tb if f.filename != "<analysis>"][-2:]
    for frame in library_frames:
        message += f"\n  in {os.path.basename(frame.filename)}:{frame.lineno} ({frame.name})"
    if isinstance(exc, KeyError):
        message += f"\n  Available columns: {df_columns}"
    return message, line_no


def _autocapture(namespace: dict, collector: Collector, go, pd) -> None:
    """Rescue outputs when generated code forgot to call the helpers."""
    for name, value in list(namespace.items()):
        if isinstance(value, go.Figure) and id(value) not in collector._figure_ids and len(collector.charts) < 3:
            collector.chart(value, title=name.replace("_", " ").title())
    if collector.kpis or collector.tables:
        return
    result = namespace.get("result")
    if isinstance(result, (pd.DataFrame, pd.Series)):
        collector.table(result, title="Result")
    elif isinstance(result, (int, float, str)) or hasattr(result, "item"):
        collector.kpi("Result", result)


def _emit(stream, payload: dict) -> None:
    stream.write("\n" + RESULT_MARKER + "\n")
    stream.write(json.dumps(payload, default=str))
    stream.flush()


def main() -> int:
    real_stdout = sys.stdout
    started = time.perf_counter()
    job = json.loads(sys.stdin.read())
    _apply_resource_limits(job.get("cpu_seconds"))

    def finish(**payload) -> int:
        payload.setdefault("kpis", [])
        payload.setdefault("charts", [])
        payload.setdefault("tables", [])
        payload.setdefault("warnings", [])
        payload.setdefault("stdout", "")
        payload["duration_ms"] = int((time.perf_counter() - started) * 1000)
        _emit(real_stdout, payload)
        return 0

    try:
        env = _prepare_environment(job)
    except Exception as exc:  # noqa: BLE001
        return finish(ok=False, error_type="SandboxSetupError", error=f"Could not prepare sandbox: {exc}")

    code = job["code"]
    try:
        compiled = compile(code, "<analysis>", "exec")
    except SyntaxError as exc:
        return finish(ok=False, error_type="SyntaxError", error_line=exc.lineno,
                      error=f"SyntaxError: {exc.msg} (line {exc.lineno})")

    collector = Collector(env["pd"], env["go"], env["pio"])
    namespace = _build_globals(env, collector, set(job["runtime_import_roots"]))
    df_columns = [str(c) for c in env["df"].columns]
    _install_audit_hook(_read_roots())

    buffer = _BoundedIO(MAX_STDOUT_CHARS)
    ok, error, error_type, error_line = True, None, None, None
    try:
        with redirect_stdout(buffer), redirect_stderr(buffer):
            exec(compiled, namespace)  # noqa: S102 — this is the sandbox
            _autocapture(namespace, collector, env["go"], env["pd"])
    except BaseException as exc:  # noqa: BLE001 — report everything, incl. SystemExit
        ok = False
        error_type = type(exc).__name__
        error, error_line = _format_exception(exc, code, df_columns)

    stdout = buffer.getvalue()
    if buffer.truncated:
        stdout += "\n… output truncated …"

    try:
        return finish(ok=ok, error=error, error_type=error_type, error_line=error_line, stdout=stdout,
                      kpis=collector.kpis, charts=collector.charts, tables=collector.tables,
                      warnings=collector.warnings)
    except Exception as exc:  # noqa: BLE001 — e.g. unserialisable output
        return finish(ok=False, error_type="SerializationError", stdout=stdout,
                      error=f"Could not serialise analysis outputs: {exc}")


if __name__ == "__main__":
    sys.exit(main())

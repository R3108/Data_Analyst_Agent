from __future__ import annotations

import pytest

from app.sandbox.policy import validate_code
from app.sandbox.runner import SandboxRunner
from tests.conftest import GOOD_CODE


@pytest.mark.parametrize("code", [
    "import os",
    "import subprocess",
    "from pandas.io import parsers",
    "open('x.txt', 'w')",
    "eval('1+1')",
    "getattr(df, 'shape')",
    "df.__class__",
    "().__class__.__bases__",
    "df.to_csv('out.csv')",
    "pd.read_csv('/etc/passwd')",
    "df.query('revenue > 1')",
    "fig.show()",
    "x = '__globals__'",
    "import numpy as np\nnp.lib",
])
def test_policy_rejects_unsafe_code(code):
    assert validate_code(code), code


def test_policy_accepts_normal_analysis():
    assert validate_code(GOOD_CODE) == []
    assert validate_code("import plotly.express as px\nfrom datetime import timedelta\nimport numpy.linalg") == []


@pytest.fixture(scope="module")
def runner() -> SandboxRunner:
    return SandboxRunner(timeout_s=60, memory_mb=2048)


def test_sandbox_runs_analysis_and_collects_outputs(runner, tiny_parquet):
    result = runner.run(GOOD_CODE + "\nimport numpy as np\nprint(np.array([1, 2]))", tiny_parquet)
    assert result.ok, result.error
    assert result.kpis[0]["label"] == "Total revenue" and result.kpis[0]["value"] == 185.0
    assert result.tables[0]["rows"][0] == ["North", 125.0]
    assert result.charts[0]["title"] == "Revenue by region"
    assert result.charts[0]["digest"]["traces"][0]["x"] == ["North", "South", "East"]
    assert "rows analysed: 4" in result.stdout


def test_sandbox_reports_runtime_errors_with_line(runner, tiny_parquet):
    result = runner.run("total = 1\nx = df['missing_column'].sum()", tiny_parquet)
    assert not result.ok
    assert result.error_type == "KeyError"
    assert result.error_line == 2
    assert "Available columns" in result.error


def test_sandbox_blocks_writes_that_pass_static_policy(runner, tiny_parquet):
    result = runner.run("df.to_json('stolen.json')", tiny_parquet)
    assert not result.ok
    assert result.error_type == "PermissionError"


def test_sandbox_does_not_expose_secrets(runner, tiny_parquet, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    result = runner.run("import json\nprint(json.dumps(sorted(dir())))", tiny_parquet)
    assert result.ok
    assert "sk-secret" not in result.stdout
    assert SandboxRunner._child_env("/tmp").get("OPENAI_API_KEY") is None


def test_sandbox_enforces_timeout(tiny_parquet):
    result = SandboxRunner(timeout_s=6, memory_mb=2048).run("while True:\n    pass", tiny_parquet)
    assert not result.ok and result.timed_out

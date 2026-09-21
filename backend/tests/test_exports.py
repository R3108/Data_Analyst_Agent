"""Deliverables: runnable notebooks, real PDFs and editable PowerPoint decks."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import REPORT, ScriptedLLM, make_plan, sign_in

CODE = """
by_region = df.groupby("Region", as_index=False)["Revenue"].sum()
kpi("Total revenue", df["Revenue"].sum(), format="currency")
kpi("Margin", 0.234, format="percent")
chart(px.bar(by_region, x="Region", y="Revenue"), title="Revenue by region", caption="North leads")
table(by_region, title="Revenue by region")
print("rows analysed:", len(df))
"""


@pytest.fixture
def client(settings):
    llm = ScriptedLLM({"plan": [make_plan()], "code": [{"approach": "group", "code": CODE}],
                       "report": [REPORT]})
    with TestClient(create_app(settings, llm=llm)) as test_client:
        yield sign_in(test_client)


@pytest.fixture
def analysed(client, tiny_csv_bytes):
    dataset = client.post("/api/datasets", files={"file": ("sales.csv", tiny_csv_bytes, "text/csv")}).json()
    session = client.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
    with client.stream("POST", f"/api/sessions/{session['id']}/chat",
                       json={"message": "Revenue by region?"}) as response:
        response.read()
    message = client.get(f"/api/sessions/{session['id']}").json()["messages"][-1]
    return dataset, session, message


@pytest.fixture
def board(client, analysed):
    _, _, message = analysed
    created = client.post("/api/boards", json={"title": "Exec dashboard"}).json()
    base = f"/api/boards/{created['id']}"
    for kind in ("kpi", "chart", "table", "insight"):
        client.post(f"{base}/items", json={"kind": kind, "message_id": message["id"], "index": 0})
    client.post(f"{base}/items", json={"kind": "note", "text": "Review **weekly**."})
    return created


def test_notebook_export_is_valid_and_runnable(client, analysed):
    _, session, _ = analysed
    response = client.get(f"/api/sessions/{session['id']}/notebook")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.ipynb"')

    notebook = json.loads(response.content)
    assert notebook["nbformat"] == 4
    assert notebook["metadata"]["kernelspec"]["name"] == "python3"
    assert notebook["metadata"]["numera"]["session_id"] == session["id"]

    code_cells = [c for c in notebook["cells"] if c["cell_type"] == "code"]
    markdown = "".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "markdown")
    shim = "".join(code_cells[0]["source"])

    # The helpers the generated code calls must be defined in the notebook itself.
    for helper in ("def kpi(", "def table(", "def chart(", "def forecast(", "def chart_forecast("):
        assert helper in shim
    assert any("groupby" in "".join(c["source"]) for c in code_cells)
    assert "Revenue by region?" in markdown
    assert REPORT["headline"] in markdown
    # Every code cell must be syntactically valid Python.
    for cell in code_cells:
        compile("".join(cell["source"]), "<cell>", "exec")


def test_notebook_records_the_cleaning_and_verification_trail(client, analysed):
    _, session, _ = analysed
    notebook = json.loads(client.get(f"/api/sessions/{session['id']}/notebook").content)
    markdown = "".join("".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "markdown")
    assert "Cleaning applied before analysis" in markdown
    assert "Verification:" in markdown


def test_session_pdf_export(client, analysed):
    _, session, _ = analysed
    response = client.get(f"/api/sessions/{session['id']}/export.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content[:5] == b"%PDF-"
    assert len(response.content) > 3000


def test_session_pptx_export_contains_native_charts(client, analysed):
    _, session, _ = analysed
    response = client.get(f"/api/sessions/{session['id']}/export.pptx")
    assert response.status_code == 200
    assert "presentationml" in response.headers["content-type"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert "ppt/presentation.xml" in names
        assert any(name.startswith("ppt/slides/slide") for name in names)
        # A real, editable chart part — not a picture of one.
        assert any(name.startswith("ppt/charts/chart") for name in names)


def test_board_pdf_and_pptx_exports(client, board):
    pdf = client.get(f"/api/boards/{board['id']}/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"

    pptx = client.get(f"/api/boards/{board['id']}/export.pptx")
    assert pptx.status_code == 200
    with zipfile.ZipFile(io.BytesIO(pptx.content)) as archive:
        assert any(n.startswith("ppt/slides/slide") for n in archive.namelist())


def test_markdown_export_includes_the_verification_verdict(client, analysed):
    _, session, _ = analysed
    text = client.get(f"/api/sessions/{session['id']}/export").text
    assert "Verification —" in text
    assert "automated checks" in text


def test_exports_of_a_missing_session_are_not_found(client):
    for path in ("notebook", "export.pdf", "export.pptx", "export"):
        assert client.get(f"/api/sessions/ses_missing/{path}").status_code == 404


def test_documents_survive_an_analysis_that_failed(client, settings, tiny_csv_bytes):
    """A failed turn still exports — the deliverable explains what went wrong."""
    from app.core.errors import LLMError

    llm = ScriptedLLM({"plan": [LLMError("rate limited", code="llm_rate_limited")]})
    with TestClient(create_app(settings, llm=llm)) as failing:
        sign_in(failing)
        dataset = failing.post("/api/datasets",
                               files={"file": ("s.csv", tiny_csv_bytes, "text/csv")}).json()
        session = failing.post("/api/sessions", json={"dataset_id": dataset["id"]}).json()
        with failing.stream("POST", f"/api/sessions/{session['id']}/chat",
                            json={"message": "hi"}) as response:
            response.read()

        assert failing.get(f"/api/sessions/{session['id']}/export.pdf").content[:5] == b"%PDF-"
        notebook = json.loads(failing.get(f"/api/sessions/{session['id']}/notebook").content)
        assert notebook["nbformat"] == 4
        assert failing.get(f"/api/sessions/{session['id']}/export.pptx").status_code == 200

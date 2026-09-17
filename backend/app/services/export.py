"""Export a chat session as a shareable Markdown report."""

from __future__ import annotations

from typing import Any


def _format_value(kpi: dict[str, Any]) -> str:
    value, fmt = kpi.get("value"), kpi.get("format")
    if value is None:
        return "—"
    if isinstance(value, (int, float)):
        if fmt == "percent":
            return f"{value * 100:.1f}%"
        if fmt == "currency":
            return f"${value:,.2f}"
        if fmt == "integer" or (isinstance(value, int)):
            return f"{value:,.0f}"
        return f"{value:,.2f}"
    return str(value)


def session_to_markdown(session: dict[str, Any], messages: list[dict[str, Any]]) -> str:
    out = [f"# {session['title']}", "", f"_Dataset: {session.get('dataset_name', session['dataset_id'])} · "
           f"exported from Numera_", ""]
    for message in messages:
        if message["role"] == "user":
            out += ["---", "", f"## ❓ {message['content']}", ""]
            continue
        payload = message.get("payload") or {}
        report = payload.get("report") or {}
        if report.get("headline"):
            out += [f"**{report['headline']}**", ""]
        out += [report.get("answer_markdown") or message["content"], ""]

        execution = payload.get("execution") or {}
        if execution.get("kpis"):
            out += ["| KPI | Value |", "| --- | --- |"]
            out += [f"| {k['label']} | {_format_value(k)} |" for k in execution["kpis"]]
            out.append("")
        for table in execution.get("tables") or []:
            cols = [c["name"] for c in table["columns"]]
            out += [f"**{table['title']}**", "", "| " + " | ".join(cols) + " |",
                    "| " + " | ".join("---" for _ in cols) + " |"]
            for row in table["rows"][:20]:
                out.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
            out.append("")
        verification = payload.get("verification")
        if verification and verification.get("score") is not None:
            out += [f"> **Verification — {verification['confidence']} confidence "
                    f"({verification['score']}/100)** across {verification.get('checks', 0)} "
                    "automated checks.", ""]
            for finding in verification.get("findings") or []:
                out.append(f"> - **{finding['title']}** ({finding['severity']}) — {finding['detail']}")
            if verification.get("findings"):
                out.append("")

        if report.get("insights"):
            out += ["### Insights", ""]
            out += [f"- **{i['title']}** — {i['detail']}" for i in report["insights"]]
            out.append("")
        if report.get("recommendations"):
            out += ["### Recommendations", ""] + [f"- {r}" for r in report["recommendations"]] + [""]
        if report.get("caveats"):
            out += ["### Caveats", ""] + [f"- {c}" for c in report["caveats"]] + [""]
        if payload.get("code"):
            out += ["<details><summary>Analysis code</summary>", "", "```python", payload["code"].rstrip(),
                    "```", "", "</details>", ""]
    return "\n".join(out)

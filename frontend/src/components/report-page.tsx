"use client";

import { useEffect, useState } from "react";

import { ReportView, type ReportSource } from "@/components/report-view";

/** `/report?session=<id>` or `/report?board=<id>`, optionally `&print=1` to open the print dialog. */
export function ReportPage() {
  const [state, setState] = useState<{ source: ReportSource | null; print: boolean } | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const session = params.get("session");
    const board = params.get("board");
    setState({
      source: session ? { sessionId: session } : board ? { boardId: board } : null,
      print: params.get("print") === "1",
    });
  }, []);

  if (!state) return null;
  if (!state.source) {
    return <p className="p-10 text-center text-sm text-ink-2">Nothing to show — open a report from an analysis or board.</p>;
  }
  return <ReportView source={state.source} autoPrint={state.print} readOnlyBadge={false} />;
}

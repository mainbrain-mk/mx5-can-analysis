"""Templated Report-Erzeugung fuer die taegliche MX-5-Pipeline - ersetzt
den bisherigen von Claude verfassten Abschlussbericht (Schritt 13/14)
durch feste, deterministische Textbausteine aus einem strukturierten
run_report-dict (siehe run_daily_pipeline.py fuer die genaue Form).

Schreibt:
  data/runs/<datum>/run_report.json  (das rohe dict, unveraendert)
  data/runs/<datum>/report.md        (Markdown, deutsch)
und haengt einen kurzen Abschnitt an docs/logs/projekt-stand.md an.

Aufruf: als Bibliothek aus run_daily_pipeline.py, siehe write_run().
"""
import json
import os

RUNS_DIR = "data/runs"
PROJEKT_STAND_PATH = "docs/logs/projekt-stand.md"


def _fmt_mass(log_id, masses):
    m = masses.get(log_id)
    if not m:
        return ""
    return f", Masse={m['mass_kg']:.1f}kg ({m['note']})" if m.get("note") else f", Masse={m['mass_kg']:.1f}kg"


def _fmt_duration(log_id, vibration):
    v = vibration.get(log_id)
    if not v or v.get("duration_s") is None:
        return ""
    return f", Dauer={v['duration_s'] / 60:.0f}min"


def render_markdown(run_report):
    r = run_report
    lines = [f"# Automatischer Lauf {r['date']}", ""]

    if not r.get("new_logs") and not r.get("new_can_logs"):
        lines.append("Keine neuen Logs gefunden.")
        return "\n".join(lines) + "\n"

    if r.get("new_can_logs"):
        lines.append(f"**{len(r['new_can_logs'])} neue(s) CAN-Log(s) vom Raspberry Pi geladen und "
                      f"in den Datalake uebernommen:**")
        for log_id in r["new_can_logs"]:
            lines.append(f"- `{log_id}`")
        lines.append("")

    if r.get("new_logs"):
        lines.append(f"**{len(r['new_logs'])} neue Log(s) verarbeitet:**")
        for log_id in r["new_logs"]:
            lines.append(f"- `{log_id}`{_fmt_duration(log_id, r.get('vibration', {}))}"
                          f"{_fmt_mass(log_id, r.get('masses', {}))}")
        lines.append("")

        lines.append("## Kernzahlen")
        for log_id in r["new_logs"]:
            b = r.get("brake", {}).get(log_id)
            c = r.get("corner", {}).get(log_id)
            parts = []
            if b:
                parts.append(f"theta={b.get('forward_direction_deg')}°, R={b.get('theta_r')}, "
                              f"Achsen={b.get('horizontal_axes')}")
            if c:
                parts.append(f"{c.get('n_events', 0)} Kurve(n) ({c.get('n_right', 0)} rechts/{c.get('n_left', 0)} links)")
            if parts:
                lines.append(f"- `{log_id}`: " + "; ".join(parts))

        dt = r.get("drivetrain")
        if dt and dt.get("median_ratio") is not None:
            lines.append(f"- Vollast-Verhaeltnis gemessen/Modell (neue Logs): Median {dt['median_ratio']:.2f} "
                          f"(n={dt.get('n', 0)})")
        ts = r.get("top_speed")
        if ts and ts.get("n_new_segments"):
            lines.append(f"- {ts['n_new_segments']} neue Top-Speed-/Gang-6-Segment(e)")
        pl = r.get("partial_load")
        if pl and pl.get("overall_rmse") is not None:
            lines.append(f"- Teillastmodell Gesamt-RMSE: {pl['overall_rmse']:.1f} Prozentpunkte")
        cd = r.get("coastdown")
        if cd and cd.get("n_new_events"):
            lines.append(f"- {cd['n_new_events']} neue Ausrollereignis(se) (insgesamt {cd.get('n_total')})")
        st = r.get("steering")
        if st and st.get("ran"):
            lines.append(f"- Lenkwinkel-Modell: k1={st.get('k1')}, R²={st.get('r2')}, n={st.get('n')}")
        lines.append("")

    findings = r.get("findings") or []
    if findings:
        lines.append("## Auffaelligkeiten")
        for f in findings:
            marker = "⚠️" if f["severity"] == "warn" else "ℹ️"
            lines.append(f"- {marker} **{f['category']}**: {f['message']}")
    else:
        lines.append("## Auffaelligkeiten")
        lines.append("Keine.")

    return "\n".join(lines) + "\n"


def render_projekt_stand_snippet(run_report):
    r = run_report
    if not r.get("new_logs") and not r.get("new_can_logs"):
        return None
    if r.get("new_logs"):
        header = f"## Automatischer Lauf: {len(r['new_logs'])} neue Logs verarbeitet ({r['date']})\n\n"
        body = [f"- `{log_id}`{_fmt_duration(log_id, r.get('vibration', {}))}"
                f"{_fmt_mass(log_id, r.get('masses', {}))}" for log_id in r["new_logs"]]
    else:
        header = f"## Automatischer Lauf: {len(r['new_can_logs'])} neue CAN-Logs uebernommen ({r['date']})\n\n"
        body = [f"- `{log_id}`" for log_id in r["new_can_logs"]]
    findings = r.get("findings") or []
    if findings:
        body.append("")
        body.append("Auffaelligkeiten:")
        body += [f"- {f['category']}: {f['message']}" for f in findings]
    else:
        body.append("")
        body.append("Keine Auffaelligkeiten.")
    return header + "\n".join(body) + "\n"


def write_run(run_report, runs_dir=RUNS_DIR, projekt_stand_path=PROJEKT_STAND_PATH):
    """Schreibt run_report.json + report.md nach runs_dir/<datum>/ und
    haengt (falls neue Logs verarbeitet wurden) einen Abschnitt an
    projekt_stand_path an. Gibt den Pfad zu report.md zurueck (oder None,
    falls keine neuen Logs - dann wird NICHTS geschrieben, siehe SKILL.md
    Schritt 1: "keine weiteren Schritte, keine Berichte")."""
    if not run_report.get("new_logs") and not run_report.get("new_can_logs"):
        return None

    run_dir = os.path.join(runs_dir, run_report["date"])
    os.makedirs(run_dir, exist_ok=True)

    with open(os.path.join(run_dir, "run_report.json"), "w", encoding="utf-8") as f:
        json.dump(run_report, f, indent=2, ensure_ascii=False)

    report_md = render_markdown(run_report)
    report_path = os.path.join(run_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    snippet = render_projekt_stand_snippet(run_report)
    if snippet:
        with open(projekt_stand_path, "a", encoding="utf-8") as f:
            f.write("\n" + snippet)

    return report_path

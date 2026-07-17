from __future__ import annotations

from io import BytesIO
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def build_battle_plan_pdf(result: dict[str, Any], trainer_label: str = "Battle Plan") -> bytes:
    """Render the line-finder result as a real, paginated PDF."""
    out = BytesIO()
    doc = SimpleDocTemplate(
        out, pagesize=letter, rightMargin=0.55 * inch, leftMargin=0.55 * inch,
        topMargin=0.55 * inch, bottomMargin=0.55 * inch,
        title=f"Battle Plan - {trainer_label}", author="Pokemon Battle Solver",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="PlanTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=23, spaceAfter=6))
    styles.add(ParagraphStyle(name="PlanMeta", parent=styles["BodyText"], textColor=colors.HexColor("#555555"), fontSize=8.5, leading=11))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=13, spaceAfter=7, borderWidth=0, textTransform="uppercase"))
    styles.add(ParagraphStyle(name="Step", parent=styles["BodyText"], fontSize=9, leading=12, spaceAfter=3))
    styles.add(ParagraphStyle(name="Small", parent=styles["BodyText"], fontSize=7.8, leading=10, textColor=colors.HexColor("#444444")))
    styles.add(ParagraphStyle(name="Confidence", parent=styles["Heading1"], alignment=TA_CENTER, fontSize=22, leading=24, spaceAfter=2))

    story: list[Any] = []
    confidence = round(float(result.get("confidence") or 0) * 100)
    realistic = round(float(result.get("realistic_confidence") or result.get("confidence") or 0) * 100)
    story += [
        Paragraph("Battle Plan", styles["PlanTitle"]),
        Paragraph(_safe(trainer_label), styles["PlanMeta"]),
        Spacer(1, 8),
        Table(
            [[Paragraph(f"<font color='white'><b>{_safe(str(result.get('result') or 'unknown')).upper()}</b></font>", styles["BodyText"]),
              Paragraph(f"{realistic}-{confidence}%", styles["Confidence"])],
             [Paragraph(_safe(str(result.get("risk_policy") or "")), styles["Small"]),
              Paragraph("luck-adjusted to structural", styles["Small"])]],
            colWidths=[5.25 * inch, 1.25 * inch],
            style=TableStyle([
                ("BOX", (0, 0), (-1, -1), 1.2, colors.black),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#111111")),
                ("TEXTCOLOR", (0, 0), (0, 0), colors.white),
                ("PADDING", (0, 0), (-1, -1), 8),
            ]),
        ),
    ]

    _section(story, "Before the battle", styles)
    prep: list[str] = []
    if result.get("field_conditions"):
        prep.append(f"Starting field: {', '.join(str(v) for v in result['field_conditions'])}")
    if result.get("heart_scales") is not None:
        prep.append(f"Heart scales available: {result['heart_scales']}")
    line_search = result.get("line_search") or {}
    if line_search.get("note"):
        prep.append(str(line_search["note"]))
    if result.get("contingency_flowchart_note"):
        prep.append(str(result["contingency_flowchart_note"]))
    for note in prep or ["Confirm held items, lead, and starting field before entering the fight."]:
        story.append(Paragraph(f"- {_safe(note)}", styles["Small"]))

    lead_positions = result.get("lead_positions") or {}
    if result.get("is_doubles") and lead_positions:
        story.append(Spacer(1, 8))
        story.append(Paragraph("<b>Doubles opening board</b>", styles["Step"]))
        player_positions = list(lead_positions.get("player") or []) + [None, None]
        enemy_positions = list(lead_positions.get("enemy") or []) + [None, None]
        board_rows = [
            ["Field position", "Your side", "Opponent"],
            ["Slot 1 (left)", _safe(player_positions[0] or "Empty"), _safe(enemy_positions[0] or "Empty")],
            ["Slot 2 (right)", _safe(player_positions[1] or "Empty"), _safe(enemy_positions[1] or "Empty")],
        ]
        story.append(Table(board_rows, colWidths=[1.5 * inch, 2.5 * inch, 2.5 * inch], repeatRows=1, style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#173a50")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#777777")),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("PADDING", (0, 0), (-1, -1), 5),
        ])))

    item_changes = ((result.get("optimized_item_line") or {}).get("item_changes") or [])
    if item_changes:
        story.append(Spacer(1, 6))
        story.append(Paragraph("<b>Recommended item loadout</b>", styles["Step"]))
        item_rows = [["Pokemon", "Current", "Equip", "Why"]]
        for change in item_changes:
            item_rows.append([
                _safe(change.get("pokemon") or "?"),
                _safe(change.get("old_item") or "None"),
                _safe(change.get("new_item") or "?"),
                Paragraph(_safe(change.get("reason") or "Improves the modeled line."), styles["Small"]),
            ])
        story.append(Table(item_rows, colWidths=[1.2 * inch, 1.0 * inch, 1.0 * inch, 3.3 * inch], repeatRows=1, style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#222222")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("PADDING", (0, 0), (-1, -1), 4),
        ])))

    _section(story, "Line 1 - exact instructions", styles)
    for turn in result.get("turns") or []:
        risks = " | ".join(str(r) for r in (turn.get("risks") or [])[:3])
        slot_actions = turn.get("slot_actions") or []
        slot_lines = []
        for action in slot_actions:
            side = "Your" if action.get("side") == "player" else "Foe"
            detail = (
                f"switch to {action.get('switch_to')}"
                if action.get("kind") == "switch"
                else f"{action.get('move')} -> {action.get('target')}"
            )
            slot_lines.append(
                f"<b>{_safe(side)} slot {int(action.get('field_slot') or 0) + 1} - {_safe(action.get('actor'))}:</b> {_safe(detail)}"
            )
        block = [
            Paragraph(f"<b>Turn {_safe(turn.get('turn'))}: {_safe(turn.get('answer'))} vs {_safe(turn.get('enemy'))}</b>", styles["Step"]),
            *([Paragraph(line, styles["Step"]) for line in slot_lines] if slot_lines else [Paragraph(_safe(turn.get("action") or ""), styles["Step"])]),
            Paragraph(f"{_safe(turn.get('calc') or '')}<br/>HP after: {_safe(turn.get('your_hp') or '?')} | Enemy: {_safe(turn.get('enemy_hp') or '?')}", styles["Small"]),
        ]
        if risks:
            block.append(Paragraph(f"<b>Risk:</b> {_safe(risks)}", styles["Small"]))
        story.append(KeepTogether([Table([[block]], colWidths=[6.5 * inch], style=TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#777777")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f6f6f6")),
            ("PADDING", (0, 0), (-1, -1), 7),
        ])), Spacer(1, 5)]))

    used = [s for s in (result.get("strategy_report") or []) if s.get("used")]
    _section(story, "Tactical game plan - strategies used", styles)
    if not used:
        story.append(Paragraph("Direct damage and clean switching; no additional tactical pattern was detected.", styles["Small"]))
    for strategy in used:
        where = " | ".join(str(v) for v in strategy.get("where") or [])
        model_note = "" if strategy.get("modeled", True) else " <i>(advisory; not fully simulated)</i>"
        story.append(KeepTogether([
            Paragraph(f"<b>{_safe(strategy.get('name'))}</b>{model_note} - {_safe(strategy.get('description'))}", styles["Step"]),
            Paragraph(f"Where: {_safe(where)}", styles["Small"]), Spacer(1, 5),
        ]))

    _section(story, "Team and enemy roster", styles)
    story.append(_roster_table(result.get("team") or [], "Your Pokemon", styles))
    story.append(Spacer(1, 8))
    story.append(_roster_table(result.get("enemies") or [], "Enemy", styles))

    threat_answers = result.get("threat_answers") or []
    if threat_answers:
        _section(story, "Best answer to each enemy", styles)
        threat_rows = [["Enemy", "Best answer", "Move", "Speed", "Check"]]
        for row in threat_answers:
            best = row.get("best") or {}
            threat_rows.append([
                _safe(row.get("enemy") or "?"), _safe(best.get("mon") or "No answer"),
                _safe(best.get("move") or "-"), "Faster" if best.get("faster") else "Slower",
                "Clean" if row.get("clean") else "Risky",
            ])
        story.append(Table(threat_rows, colWidths=[1.45 * inch, 1.45 * inch, 1.55 * inch, 0.9 * inch, 1.15 * inch], repeatRows=1, style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("PADDING", (0, 0), (-1, -1), 4),
        ])))

    story.append(PageBreak())
    _section(story, "Full strategy toolbox", styles)
    for strategy in result.get("strategy_report") or []:
        marker = "USED" if strategy.get("used") and strategy.get("modeled", True) else "ADVISORY" if not strategy.get("modeled", True) else "OPTION"
        story.append(KeepTogether([
            Paragraph(f"<b>[{marker}] {_safe(strategy.get('name'))}</b> - {_safe(strategy.get('description'))}", styles["Step"]),
            Paragraph(f"Consider when: {_safe(strategy.get('when') or 'the matchup calls for it')}", styles["Small"]),
            Paragraph(f"Viable if: {_safe(strategy.get('viable') or 'the relevant damage and speed checks hold')}", styles["Small"]),
            Spacer(1, 6),
        ]))

    def footer(canvas: Any, document: Any) -> None:
        canvas.saveState()
        # Paint an explicit page rather than relying on a viewer's default canvas color.
        # This keeps previews, print workflows, and transparent PDF rasterizers legible.
        canvas.setFillColor(colors.white)
        canvas.rect(0, 0, letter[0], letter[1], stroke=0, fill=1)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawString(0.55 * inch, 0.3 * inch, "Pokemon Battle Solver - modeled plan, not a guaranteed win rate")
        canvas.drawRightString(7.95 * inch, 0.3 * inch, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return out.getvalue()


def _section(story: list[Any], title: str, styles: Any) -> None:
    story.append(Paragraph(_safe(title), styles["Section"]))
    story.append(Table([[""]], colWidths=[6.5 * inch], rowHeights=[1.2], style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.black)])))
    story.append(Spacer(1, 6))


def _roster_table(rows: list[dict[str, Any]], heading: str, styles: Any) -> Table:
    data = [[Paragraph(f"<font color='white'><b>{_safe(heading)}</b></font>", styles["Small"]), "HP", "Item", "Moves"]]
    for row in rows:
        name = row.get("name") or row.get("species") or "?"
        hp = f"{row.get('hp', '?')}/{row.get('max_hp', '?')}"
        data.append([Paragraph(_safe(name), styles["Small"]), hp, _safe(row.get("item") or "-"), Paragraph(_safe(" / ".join(row.get("moves") or [])), styles["Small"])])
    return Table(data, colWidths=[1.25 * inch, 0.65 * inch, 1.0 * inch, 3.6 * inch], repeatRows=1, style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#111111")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("PADDING", (0, 0), (-1, -1), 4),
    ]))


def _safe(value: Any) -> str:
    return str(value if value is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

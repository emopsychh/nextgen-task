"""Generate a downloadable PDF for a work report."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from board.reports import deal_hours_for_portal, report_detail_metrics, report_portal_id
from board.timeutils import format_duration_ru

STATUS_LABEL_RU = {
    "draft": "На рассмотрении руководителя",
    "pending_client": "Требует рассмотрения",
    "disputed": "Связь с менеджером",
    "accepted": "Согласован",
    "paid": "Согласован",
    "dismissed": "Снято с контроля",
}

TASK_STATUS_RU = {
    "todo": "К выполнению",
    "in_progress": "В работе",
    "done": "Готово",
}

_FONT_REGULAR = "ReportFont"
_FONT_BOLD = "ReportFont-Bold"


def _font_candidates() -> list[tuple[str, str]]:
    """(regular, bold) TTF pairs that support Cyrillic."""
    pairs: list[tuple[str, str]] = []
    # Linux (Docker: fonts-dejavu-core)
    pairs.append(
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    )
    # Local Windows
    windir = Path(r"C:\Windows\Fonts")
    if windir.is_dir():
        for regular, bold in (
            ("arial.ttf", "arialbd.ttf"),
            ("segoeui.ttf", "segoeuib.ttf"),
            ("calibri.ttf", "calibrib.ttf"),
        ):
            r = windir / regular
            b = windir / bold
            if r.is_file() and b.is_file():
                pairs.append((str(r), str(b)))
    # Optional vendored fonts next to this module
    local = Path(__file__).resolve().parent / "fonts"
    if (local / "DejaVuSans.ttf").is_file() and (local / "DejaVuSans-Bold.ttf").is_file():
        pairs.insert(
            0,
            (str(local / "DejaVuSans.ttf"), str(local / "DejaVuSans-Bold.ttf")),
        )
    return pairs


@lru_cache(maxsize=1)
def _register_fonts() -> tuple[str, str]:
    for regular, bold in _font_candidates():
        if Path(regular).is_file() and Path(bold).is_file():
            pdfmetrics.registerFont(TTFont(_FONT_REGULAR, regular))
            pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
            return _FONT_REGULAR, _FONT_BOLD
    raise RuntimeError(
        "Не найден TTF-шрифт с кириллицей (DejaVu Sans / Arial). "
        "Установите fonts-dejavu-core или положите файлы в board/fonts/."
    )


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _fmt_hours_package(value) -> str:
    if value is None:
        return "—"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    if n == int(n):
        return f"{int(n)} ч"
    return f"{n:g} ч"


def _report_title(report, project_names: list[str]) -> str:
    if not project_names:
        return f"Отчёт №{report.id}"
    if len(project_names) == 1:
        return project_names[0]
    if len(project_names) == 2:
        return f"{project_names[0]} и {project_names[1]}"
    return f"{project_names[0]} и ещё {len(project_names) - 1}"


def build_report_pdf(report) -> tuple[bytes, str]:
    """Return (pdf_bytes, download_filename)."""
    font, font_bold = _register_fonts()
    metrics = report_detail_metrics(report)
    project_names = list(metrics.get("project_names") or [])
    projects_detail = list(metrics.get("projects_detail") or [])
    total_seconds = int(metrics.get("total_tracked_seconds") or 0)

    portal_name = ""
    if getattr(report, "portal_id", None) and report.portal:
        portal_name = report.portal.name or report.portal.domain or ""
    elif getattr(report, "project_id", None) and report.project:
        portal_name = report.project.portal.name or report.project.portal.domain or ""

    title = _report_title(report, project_names)
    status_label = STATUS_LABEL_RU.get(report.status, report.status)
    created = timezone.localtime(report.created_at).strftime("%d.%m.%Y %H:%M")

    portal_id = report_portal_id(report)
    deal = deal_hours_for_portal(portal_id) if portal_id else None

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=title,
        author="NextGen Task",
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "ReportTitle",
        parent=styles["Heading1"],
        fontName=font_bold,
        fontSize=16,
        leading=20,
        spaceAfter=6,
        textColor=colors.HexColor("#1a1a1a"),
    )
    style_meta = ParagraphStyle(
        "ReportMeta",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#555555"),
        spaceAfter=2,
    )
    style_h2 = ParagraphStyle(
        "ReportH2",
        parent=styles["Heading2"],
        fontName=font_bold,
        fontSize=12,
        leading=16,
        spaceBefore=12,
        spaceAfter=6,
        textColor=colors.HexColor("#1a1a1a"),
    )
    style_body = ParagraphStyle(
        "ReportBody",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9.5,
        leading=13,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#222222"),
    )
    style_muted = ParagraphStyle(
        "ReportMuted",
        parent=style_body,
        textColor=colors.HexColor("#666666"),
    )
    style_cell = ParagraphStyle(
        "ReportCell",
        parent=style_body,
        fontSize=9,
        leading=12,
    )
    style_cell_bold = ParagraphStyle(
        "ReportCellBold",
        parent=style_cell,
        fontName=font_bold,
    )

    story: list = []
    story.append(Paragraph(_esc(title), style_title))
    story.append(Paragraph(f"Клиент: {_esc(portal_name or '—')}", style_meta))
    story.append(Paragraph(f"Статус: {_esc(status_label)}", style_meta))
    story.append(Paragraph(f"Создан: {_esc(created)}", style_meta))
    if len(project_names) > 1:
        story.append(
            Paragraph(f"Проекты: {_esc(' · '.join(project_names))}", style_meta)
        )
    story.append(Spacer(1, 8))

    summary_rows = [
        [
            Paragraph("<b>Затрачено</b>", style_cell_bold),
            Paragraph(_esc(format_duration_ru(total_seconds)), style_cell),
        ],
        [
            Paragraph("<b>Проекты</b>", style_cell_bold),
            Paragraph(str(len(projects_detail)), style_cell),
        ],
        [
            Paragraph("<b>Задачи</b>", style_cell_bold),
            Paragraph(
                str(sum(len(p.get("tasks") or []) for p in projects_detail)),
                style_cell,
            ),
        ],
    ]
    if deal:
        rem = _fmt_hours_package(deal.get("remaining_hours"))
        paid = _fmt_hours_package(deal.get("paid_hours"))
        summary_rows.append(
            [
                Paragraph("<b>Остаток по сделке</b>", style_cell_bold),
                Paragraph(_esc(f"{rem} из {paid}"), style_cell),
            ]
        )

    summary = Table(summary_rows, colWidths=[45 * mm, 125 * mm])
    summary.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
                ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#e5e5e5")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(summary)

    if report.status == "disputed" and (report.client_comment or "").strip():
        story.append(Paragraph("Комментарий клиента", style_h2))
        story.append(Paragraph(_esc(report.client_comment.strip()), style_body))

    for block in projects_detail:
        pname = block.get("name") or "Проект"
        psecs = int(block.get("total_tracked_seconds") or 0)
        story.append(
            Paragraph(
                f"{_esc(pname)} — {_esc(format_duration_ru(psecs))}",
                style_h2,
            )
        )
        tasks = list(block.get("tasks") or [])
        if not tasks:
            story.append(Paragraph("Нет задач", style_muted))
            continue

        rows = [
            [
                Paragraph("<b>Задача</b>", style_cell_bold),
                Paragraph("<b>Статус</b>", style_cell_bold),
                Paragraph("<b>Время</b>", style_cell_bold),
                Paragraph("<b>Итог</b>", style_cell_bold),
            ]
        ]
        for task in tasks:
            t_status = TASK_STATUS_RU.get(task.get("status") or "", task.get("status") or "—")
            outcome = (task.get("outcome") or "").strip() or "—"
            rows.append(
                [
                    Paragraph(_esc(task.get("title") or "—"), style_cell),
                    Paragraph(_esc(t_status), style_cell),
                    Paragraph(
                        _esc(format_duration_ru(int(task.get("tracked_seconds") or 0))),
                        style_cell,
                    ),
                    Paragraph(_esc(outcome), style_cell),
                ]
            )

        table = Table(rows, colWidths=[58 * mm, 28 * mm, 22 * mm, 62 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ececec")),
                    ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)

    story.append(Spacer(1, 14))
    story.append(
        Paragraph(
            f"Сформировано в NextGen Task · {timezone.localtime().strftime('%d.%m.%Y %H:%M')}",
            style_muted,
        )
    )

    doc.build(story)
    filename = f"report-{report.id}.pdf"
    return buf.getvalue(), filename

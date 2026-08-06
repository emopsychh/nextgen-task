"""Generate a downloadable PDF for a work report (nextgen.consulting style)."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

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

# nextgen.consulting palette
_CREAM = colors.HexColor("#FBF8F1")
_INK = colors.HexColor("#1A1A1A")
_MUTED = colors.HexColor("#7A7A7A")
_YELLOW = colors.HexColor("#FFD34E")
_YELLOW_SOFT = colors.HexColor("#FFF3C4")
_LINE = colors.HexColor("#EDE6D8")
_WHITE = colors.white
_SOFT = colors.HexColor("#F6F1E6")

_FONT_REGULAR = "ReportFont"
_FONT_BOLD = "ReportFont-Bold"

_ASSETS = Path(__file__).resolve().parent / "assets"
_LOGO_PATH = _ASSETS / "logo-circle.png"
if not _LOGO_PATH.is_file():
    _LOGO_PATH = _ASSETS / "logo.png"


def _font_candidates() -> list[tuple[str, str]]:
    """(regular, bold) TTF pairs that support Cyrillic."""
    pairs: list[tuple[str, str]] = []
    pairs.append(
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        )
    )
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
    total_minutes = max(0, int(round(n * 60)))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    return f"{minutes} мин"


def _format_duration_ru(total_seconds: int) -> str:
    seconds = max(0, int(total_seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} ч")
    if minutes or (hours and not secs):
        parts.append(f"{minutes} мин")
    elif not hours:
        if secs and not minutes:
            parts.append(f"{secs} сек")
        else:
            parts.append(f"{minutes} мин")
    if hours and secs and not minutes:
        parts.append(f"{secs} сек")
    return " ".join(parts) if parts else "0 мин"


def _report_title(report, project_names: list[str]) -> str:
    if not project_names:
        return f"Отчёт №{report.id}"
    if len(project_names) == 1:
        return project_names[0]
    if len(project_names) == 2:
        return f"{project_names[0]} и {project_names[1]}"
    return f"{project_names[0]} и ещё {len(project_names) - 1}"


class _RoundedCard(Flowable):
    """White rounded card with optional yellow top accent."""

    def __init__(
        self,
        content: Flowable,
        *,
        width: float,
        padding: float = 10,
        radius: float = 10,
        fill=_WHITE,
        stroke=_LINE,
        accent=None,
        min_height: float = 0,
    ):
        super().__init__()
        self.content = content
        self.card_width = width
        self.padding = padding
        self.radius = radius
        self.fill = fill
        self.stroke = stroke
        self.accent = accent
        self.min_height = min_height
        self._inner_w = width - 2 * padding
        self._content_h = 0
        self._height = 0

    def wrap(self, availWidth, availHeight):
        # Fill the width assigned by the parent table/frame so sibling
        # blocks share identical outer edges.
        if availWidth and availWidth > 0:
            self.card_width = float(availWidth)
            self._inner_w = max(self.card_width - 2 * self.padding, 8)
            if isinstance(self.content, Table) and len(getattr(self.content, "_argW", []) or []) == 1:
                self.content._argW = [self._inner_w]
        w, h = self.content.wrap(self._inner_w, availHeight)
        self._content_h = h
        self._height = max(self.min_height, h + 2 * self.padding)
        return self.card_width, self._height

    def draw(self):
        c = self.canv
        c.saveState()
        # Clip all fills to the rounded card so accents never spill.
        path = c.beginPath()
        path.roundRect(0, 0, self.card_width, self._height, self.radius)
        c.clipPath(path, stroke=0)
        c.setFillColor(self.fill)
        c.rect(0, 0, self.card_width, self._height, fill=1, stroke=0)
        if self.accent is not None:
            c.setFillColor(self.accent)
            c.rect(0, self._height - 3.2, self.card_width, 3.2, fill=1, stroke=0)
        c.restoreState()

        c.saveState()
        c.setStrokeColor(self.stroke)
        c.setLineWidth(0.7)
        c.roundRect(0, 0, self.card_width, self._height, self.radius, fill=0, stroke=1)
        self.content.drawOn(c, self.padding, self.padding)
        c.restoreState()


class _ProgressBar(Flowable):
    """Horizontal remaining/used bar for the deal package."""

    def __init__(self, width: float, remaining_ratio: float, height: float = 8):
        super().__init__()
        self.bar_width = width
        self.height = height
        self.remaining_ratio = max(0.0, min(1.0, float(remaining_ratio)))

    def wrap(self, availWidth, availHeight):
        return self.bar_width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        radius = self.height / 2
        c.setFillColor(_LINE)
        c.roundRect(0, 0, self.bar_width, self.height, radius, fill=1, stroke=0)
        fill_w = self.bar_width * self.remaining_ratio
        if fill_w > 0.5:
            c.setFillColor(_YELLOW)
            c.roundRect(0, 0, max(fill_w, radius * 2), self.height, radius, fill=1, stroke=0)
        c.restoreState()


class _AccentDot(Flowable):
    def __init__(self, size=3.2, color=_YELLOW):
        super().__init__()
        self.size = size
        self.color = color

    def wrap(self, availWidth, availHeight):
        return self.size + 2, self.size + 2

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.circle(
            self.size / 2 + 1, self.size / 2 + 1, self.size / 2, fill=1, stroke=0
        )


def _draw_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(_CREAM)
    canvas.rect(0, 0, A4[0], A4[1], fill=1, stroke=0)

    # Soft decorative blobs
    canvas.setFillColor(_YELLOW_SOFT)
    canvas.circle(A4[0] - 8 * mm, A4[1] - 18 * mm, 28 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.HexColor("#FFE8A3"))
    canvas.circle(22 * mm, A4[1] + 4 * mm, 20 * mm, fill=1, stroke=0)

    canvas.setFillColor(_MUTED)
    canvas.setFont(_FONT_REGULAR, 8)
    canvas.drawString(20 * mm, 11 * mm, "nextgen.consulting")
    canvas.drawRightString(A4[0] - 20 * mm, 11 * mm, f"{doc.page}")
    canvas.restoreState()


def _build_styles(font: str, font_bold: str) -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "eyebrow": ParagraphStyle(
            "NgEyebrow",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            spaceAfter=0,
        ),
        "brand": ParagraphStyle(
            "NgBrand",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=14,
            leading=17,
            textColor=_INK,
        ),
        "brand_sub": ParagraphStyle(
            "NgBrandSub",
            parent=base["Normal"],
            fontName=font,
            fontSize=8.5,
            leading=11,
            textColor=_MUTED,
        ),
        "title": ParagraphStyle(
            "NgTitle",
            parent=base["Heading1"],
            fontName=font_bold,
            fontSize=22,
            leading=27,
            spaceBefore=0,
            spaceAfter=6,
            textColor=_INK,
        ),
        "meta": ParagraphStyle(
            "NgMeta",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
        ),
        "meta_strong": ParagraphStyle(
            "NgMetaStrong",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=9,
            leading=12,
            textColor=_INK,
        ),
        "section": ParagraphStyle(
            "NgSection",
            parent=base["Heading2"],
            fontName=font_bold,
            fontSize=13,
            leading=17,
            spaceBefore=0,
            spaceAfter=0,
            textColor=_INK,
        ),
        "section_meta": ParagraphStyle(
            "NgSectionMeta",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
            alignment=TA_RIGHT,
        ),
        "body": ParagraphStyle(
            "NgBody",
            parent=base["Normal"],
            fontName=font,
            fontSize=9.5,
            leading=13,
            alignment=TA_LEFT,
            textColor=_INK,
        ),
        "muted": ParagraphStyle(
            "NgMuted",
            parent=base["Normal"],
            fontName=font,
            fontSize=9,
            leading=12,
            textColor=_MUTED,
        ),
        "stat_label": ParagraphStyle(
            "NgStatLabel",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
        ),
        "stat_value": ParagraphStyle(
            "NgStatValue",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=14,
            leading=17,
            textColor=_INK,
        ),
        "cell": ParagraphStyle(
            "NgCell",
            parent=base["Normal"],
            fontName=font,
            fontSize=8.5,
            leading=11,
            textColor=_INK,
        ),
        "cell_bold": ParagraphStyle(
            "NgCellBold",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
        ),
        "cell_muted": ParagraphStyle(
            "NgCellMuted",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
        ),
        "deal_kicker": ParagraphStyle(
            "NgDealKicker",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=8,
            leading=10,
            textColor=_MUTED,
        ),
        "deal_big": ParagraphStyle(
            "NgDealBig",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=16,
            leading=19,
            textColor=_INK,
        ),
        "deal_small": ParagraphStyle(
            "NgDealSmall",
            parent=base["Normal"],
            fontName=font,
            fontSize=8.5,
            leading=11,
            textColor=_MUTED,
        ),
        "deal_small_value": ParagraphStyle(
            "NgDealSmallValue",
            parent=base["Normal"],
            fontName=font_bold,
            fontSize=10,
            leading=13,
            textColor=_INK,
        ),
        "footer_note": ParagraphStyle(
            "NgFooterNote",
            parent=base["Normal"],
            fontName=font,
            fontSize=8,
            leading=11,
            textColor=_MUTED,
            alignment=TA_LEFT,
        ),
    }


def _header_block(styles: dict[str, ParagraphStyle]) -> Flowable:
    brand = Table(
        [
            [Paragraph("nextgen <font color='#9A9A9A'>.consulting</font>", styles["brand"])],
            [Paragraph("Отчёт о выполненных работах", styles["brand_sub"])],
        ],
        colWidths=[130 * mm],
    )
    brand.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    if _LOGO_PATH.is_file():
        logo = Image(str(_LOGO_PATH), width=15 * mm, height=15 * mm, mask="auto")
        row = Table([[logo, brand]], colWidths=[18 * mm, 152 * mm])
    else:
        row = Table([[brand]], colWidths=[170 * mm])
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return row


def _metric_card(label: str, value: str, styles: dict[str, ParagraphStyle], width: float) -> Flowable:
    inner_w = max(width - 22, 20)
    inner = Table(
        [
            [Paragraph(_esc(label.upper()), styles["stat_label"])],
            [Paragraph(_esc(value), styles["stat_value"])],
        ],
        colWidths=[inner_w],
    )
    inner.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 1), (0, 1), 0),
            ]
        )
    )
    return _RoundedCard(
        inner,
        width=width,
        padding=11,
        radius=12,
        accent=_YELLOW,
        min_height=22 * mm,
    )


def _metrics_row(
    items: list[tuple[str, str]],
    styles: dict[str, ParagraphStyle],
    *,
    content_w: float,
    gap: float = 3.5 * mm,
) -> Table:
    """Equal-width metric cards whose outer edges match content_w exactly."""
    n = len(items)
    if n == 0:
        return Table([[]])

    # Exact arithmetic: last card absorbs float remainder so sum == content_w.
    gaps_total = gap * (n - 1)
    base = (content_w - gaps_total) / n
    widths = [base] * (n - 1) + [content_w - gaps_total - base * (n - 1)]
    cards = [
        _metric_card(label, value, styles, widths[i]) for i, (label, value) in enumerate(items)
    ]

    row: list = []
    cols: list[float] = []
    for i, card in enumerate(cards):
        if i:
            row.append(Spacer(gap, 1))
            cols.append(gap)
        row.append(card)
        cols.append(widths[i])

    table = Table([row], colWidths=cols)
    table.hAlign = "LEFT"
    table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ]
        )
    )
    return table


def _summary_section(
    *,
    styles: dict[str, ParagraphStyle],
    content_w: float,
    total_seconds: int,
    projects_count: int,
    tasks_count: int,
    deal: dict | None,
) -> Table:
    """Metrics + deal package in one fixed-width column (guaranteed alignment)."""
    metrics = _metrics_row(
        [
            ("Затрачено", _format_duration_ru(total_seconds)),
            ("Проекты", str(projects_count)),
            ("Задачи", str(tasks_count)),
        ],
        styles,
        content_w=content_w,
        gap=3.5 * mm,
    )
    rows: list = [[metrics]]
    if deal:
        rows.append([Spacer(1, 10)])
        rows.append([_deal_hours_block(deal, styles, content_w)])

    stack = Table(rows, colWidths=[content_w])
    stack.hAlign = "LEFT"
    stack.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ]
        )
    )
    return stack


def _meta_pills(
    portal_name: str,
    status_label: str,
    created: str,
    project_names: list[str],
    styles: dict[str, ParagraphStyle],
    width: float,
) -> Flowable:
    bits = [
        f"<b>Клиент</b>  {_esc(portal_name or '—')}",
        f"<b>Создан</b>  {_esc(created)}",
    ]
    text = "   ·   ".join(bits)
    inner = Paragraph(text, styles["meta"])
    return _RoundedCard(inner, width=width, padding=10, radius=12, fill=_SOFT, stroke=_LINE)


def _parse_hours(value) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # NaN guard


def _deal_hours_block(deal: dict, styles: dict[str, ParagraphStyle], width: float) -> Flowable:
    paid_n = _parse_hours(deal.get("paid_hours"))
    rem_n = _parse_hours(deal.get("remaining_hours"))
    used_n = None
    if paid_n is not None and rem_n is not None:
        used_n = max(0.0, paid_n - max(0.0, rem_n))
    ratio = 0.0
    if paid_n and paid_n > 0 and rem_n is not None:
        ratio = max(0.0, min(1.0, max(0.0, rem_n) / paid_n))

    rem_label = _fmt_hours_package(rem_n)
    paid_label = _fmt_hours_package(paid_n)
    used_label = _fmt_hours_package(used_n) if used_n is not None else "—"
    pct_label = f"{int(round(ratio * 100))}% пакета ещё доступно" if paid_n else ""

    inner_w = width - 28
    left_w = inner_w * 0.55
    side_w = inner_w - left_w

    left = Table(
        [
            [Paragraph("ПАКЕТ СОПРОВОЖДЕНИЯ", styles["deal_kicker"])],
            [Paragraph(_esc(rem_label), styles["deal_big"])],
            [Paragraph("осталось", styles["deal_small"])],
        ],
        colWidths=[left_w],
    )
    left.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 1), (0, 1), 1),
                ("BOTTOMPADDING", (0, 2), (0, 2), 0),
            ]
        )
    )

    side = Table(
        [
            [
                Paragraph("Использовано", styles["deal_small"]),
                Paragraph(_esc(used_label), styles["deal_small_value"]),
            ],
            [
                Paragraph("В пакете", styles["deal_small"]),
                Paragraph(_esc(paid_label), styles["deal_small_value"]),
            ],
        ],
        colWidths=[side_w * 0.48, side_w * 0.52],
    )
    side.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )

    top = Table([[left, side]], colWidths=[left_w, side_w])
    top.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )

    body_rows: list = [[top], [Spacer(1, 10)], [_ProgressBar(inner_w, ratio)]]
    if pct_label:
        body_rows.extend([[Spacer(1, 5)], [Paragraph(_esc(pct_label), styles["deal_small"])]])

    body = Table(body_rows, colWidths=[inner_w])
    body.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return _RoundedCard(
        body,
        width=width,
        padding=14,
        radius=12,
        fill=_WHITE,
        stroke=_LINE,
        accent=_YELLOW,
    )


def _tasks_table(tasks: list[dict], styles: dict[str, ParagraphStyle], width: float) -> Table:
    col_task = width * 0.38
    col_status = width * 0.16
    col_time = width * 0.14
    col_outcome = width - col_task - col_status - col_time

    rows = [
        [
            Paragraph("Задача", styles["cell_bold"]),
            Paragraph("Статус", styles["cell_bold"]),
            Paragraph("Время", styles["cell_bold"]),
            Paragraph("Итог", styles["cell_bold"]),
        ]
    ]
    for task in tasks:
        status_key = task.get("status") or ""
        status_label = TASK_STATUS_RU.get(status_key, status_key or "—")
        outcome = (task.get("outcome") or "").strip() or "—"
        rows.append(
            [
                Paragraph(_esc(task.get("title") or "—"), styles["cell"]),
                Paragraph(_esc(status_label), styles["cell_muted"]),
                Paragraph(
                    _esc(_format_duration_ru(int(task.get("tracked_seconds") or 0))),
                    styles["cell"],
                ),
                Paragraph(_esc(outcome), styles["cell_muted"]),
            ]
        )

    table = Table(
        rows,
        colWidths=[col_task, col_status, col_time, col_outcome],
        repeatRows=1,
    )
    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), _SOFT),
        ("LINEBELOW", (0, 0), (-1, 0), 1.5, _YELLOW),
        ("BOX", (0, 0), (-1, -1), 0.6, _LINE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, _LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 7),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 7),
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("BACKGROUND", (0, 1), (-1, -1), _WHITE),
    ]
    for i in range(2, len(rows), 2):
        cmds.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FCFAF5")))
    table.setStyle(TableStyle(cmds))
    return table


def _section_heading(name: str, duration: str, styles: dict[str, ParagraphStyle]) -> Flowable:
    left = Table(
        [
            [_AccentDot(), Paragraph(_esc(name), styles["section"])],
        ],
        colWidths=[6 * mm, 120 * mm],
    )
    left.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    right = Paragraph(_esc(duration), styles["section_meta"])
    row = Table([[left, right]], colWidths=[130 * mm, 40 * mm])
    row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return row


def build_report_pdf_bytes(
    *,
    title: str,
    portal_name: str,
    status_label: str,
    created: str,
    project_names: list[str],
    projects_detail: list[dict],
    total_seconds: int,
    deal: dict | None = None,
    client_comment: str | None = None,
    generated_at: str | None = None,
) -> bytes:
    """Render PDF bytes from already-prepared report data."""
    font, font_bold = _register_fonts()
    styles = _build_styles(font, font_bold)
    content_w = 170 * mm

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="nextgen.consulting",
    )

    story: list = []
    story.append(_header_block(styles))
    story.append(Spacer(1, 14))

    story.append(Paragraph("Отчёт по проектам", styles["eyebrow"]))
    story.append(Spacer(1, 2))
    story.append(Paragraph(_esc(title or "Отчёт"), styles["title"]))
    story.append(
        _meta_pills(
            portal_name, status_label, created, project_names, styles, content_w
        )
    )
    story.append(Spacer(1, 12))

    task_count = sum(len(p.get("tasks") or []) for p in projects_detail)
    story.append(
        _summary_section(
            styles=styles,
            content_w=content_w,
            total_seconds=total_seconds,
            projects_count=len(projects_detail),
            tasks_count=task_count,
            deal=deal,
        )
    )

    if client_comment:
        story.append(Spacer(1, 12))
        story.append(_section_heading("Комментарий клиента", "", styles))
        story.append(Spacer(1, 6))
        story.append(
            _RoundedCard(
                Paragraph(_esc(client_comment), styles["body"]),
                width=content_w,
                padding=12,
                radius=12,
            )
        )

    for block in projects_detail:
        pname = block.get("name") or "Проект"
        psecs = int(block.get("total_tracked_seconds") or 0)
        story.append(Spacer(1, 14))
        story.append(_section_heading(pname, _format_duration_ru(psecs), styles))
        story.append(Spacer(1, 6))
        tasks = list(block.get("tasks") or [])
        if not tasks:
            story.append(Paragraph("Нет задач", styles["muted"]))
            continue
        story.append(_tasks_table(tasks, styles, content_w))

    story.append(Spacer(1, 10))
    stamp = generated_at or ""
    story.append(
        Paragraph(
            "Сформировано nextgen.consulting"
            + (f" · {_esc(stamp)}" if stamp else ""),
            styles["footer_note"],
        )
    )

    doc.build(story, onFirstPage=_draw_page, onLaterPages=_draw_page)
    return buf.getvalue()


def build_report_pdf(report) -> tuple[bytes, str]:
    """Return (pdf_bytes, download_filename)."""
    from django.utils import timezone

    from board.reports import deal_hours_for_portal, report_detail_metrics, report_portal_id

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
    generated = timezone.localtime().strftime("%d.%m.%Y %H:%M")

    portal_id = report_portal_id(report)
    deal = deal_hours_for_portal(portal_id) if portal_id else None
    comment = None
    if report.status == "disputed" and (report.client_comment or "").strip():
        comment = report.client_comment.strip()

    pdf_bytes = build_report_pdf_bytes(
        title=title,
        portal_name=portal_name,
        status_label=status_label,
        created=created,
        project_names=project_names,
        projects_detail=projects_detail,
        total_seconds=total_seconds,
        deal=deal,
        client_comment=comment,
        generated_at=generated,
    )
    return pdf_bytes, f"report-{report.id}.pdf"


def write_sample_report_pdf(path: str | Path) -> Path:
    """Write a demo PDF with sample content for visual review."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pdf = build_report_pdf_bytes(
        title="Сопровождение Bitrix24",
        portal_name="chicaga",
        status_label="Требует рассмотрения",
        created="06.08.2026 12:40",
        project_names=["Сопровождение Bitrix24", "Интеграции"],
        projects_detail=[
            {
                "name": "Сопровождение Bitrix24",
                "total_tracked_seconds": 7440,
                "tasks": [
                    {
                        "title": "Убрать стадию тех.перенос",
                        "status": "done",
                        "tracked_seconds": 2021,
                        "outcome": "Стадия скрыта, процессы обновлены",
                    },
                    {
                        "title": "Рассылки троятся",
                        "status": "in_progress",
                        "tracked_seconds": 1800,
                        "outcome": "Нашли дубли в роботе, правим условия",
                    },
                    {
                        "title": "Дублирование карточек отчёта по месяцам",
                        "status": "done",
                        "tracked_seconds": 3559,
                        "outcome": "Исправлена логика формирования карточек",
                    },
                ],
            },
            {
                "name": "Интеграции",
                "total_tracked_seconds": 3600,
                "tasks": [
                    {
                        "title": "Синхронизация сделок с сайтом",
                        "status": "todo",
                        "tracked_seconds": 3600,
                        "outcome": "Черновик схемы обмена",
                    },
                ],
            },
        ],
        total_seconds=11040,
        deal={"paid_hours": 10, "remaining_hours": 7.93},
        generated_at="06.08.2026 16:30",
    )
    out.write_bytes(pdf)
    return out

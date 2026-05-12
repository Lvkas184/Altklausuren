from __future__ import annotations

import copy
import re
import shutil
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


class PdfProcessingError(Exception):
    pass


def append_submission(
    *,
    subject: dict,
    subject_dir: Path,
    upload_path: Path,
    metadata: dict,
    strip_uploaded_cover: bool,
) -> dict:
    current_path = subject_dir / "current.pdf"
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    try:
        incoming_reader = PdfReader(str(upload_path))
    except Exception as exc:
        raise PdfProcessingError("Die hochgeladene Datei konnte nicht als PDF gelesen werden.") from exc

    if len(incoming_reader.pages) == 0:
        raise PdfProcessingError("Die hochgeladene PDF enthält keine Seiten.")

    uploaded_start = 1 if strip_uploaded_cover and len(incoming_reader.pages) > 1 else 0
    if strip_uploaded_cover and len(incoming_reader.pages) == 1:
        raise PdfProcessingError("Das Deckblatt kann nicht entfernt werden, weil die PDF nur eine Seite hat.")

    writer = PdfWriter()
    existing_body_pages = 0

    entries = list(subject.get("submissions", []))
    entries.append(metadata)
    cover_pdf = _build_cover(subject, entries)
    cover_reader = PdfReader(cover_pdf)
    writer.add_page(cover_reader.pages[0])

    added_pages = 0
    for page in incoming_reader.pages[uploaded_start:]:
        writer.add_page(page)
        added_pages += 1

    if current_path.exists():
        backup_path = archive_dir / f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        shutil.copy2(current_path, backup_path)
        try:
            current_reader = PdfReader(str(current_path))
        except Exception as exc:
            raise PdfProcessingError("Die bestehende Sammlung konnte nicht als PDF gelesen werden.") from exc

        for page in current_reader.pages[1:]:
            writer.add_page(page)
            existing_body_pages += 1

    exports_dir = subject_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_path = exports_dir / f"{subject['slug']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    with current_path.open("wb") as output:
        writer.write(output)
    shutil.copy2(current_path, export_path)
    generate_single_page_pdf(current_path)

    return {
        "stored_upload": str(upload_path.relative_to(subject_dir)),
        "added_pages": added_pages,
        "existing_body_pages": existing_body_pages,
        "current_pages": len(writer.pages),
        "export_path": str(export_path.relative_to(subject_dir)),
    }


def regenerate_current_pdf(*, subject: dict, subject_dir: Path) -> dict:
    current_path = subject_dir / "current.pdf"
    archive_dir = subject_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    submissions = sorted(
        subject.get("submissions", []),
        key=lambda item: (
            tuple(-x for x in _semester_sort_key(item.get("term", ""))),
            int(item.get("sort_order") or 0),
            item.get("added_at", ""),
        ),
    )

    if current_path.exists():
        backup_path = archive_dir / f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
        shutil.copy2(current_path, backup_path)

    if len(submissions) == 1 and submissions[0].get("collection_import"):
        source = _stored_upload_path(subject_dir, submissions[0])
        _validate_pdf(source)
        shutil.copy2(source, current_path)
        page_count = len(PdfReader(str(current_path)).pages)
        return {"current_pages": page_count, "regenerated": True, "preserved_import": True}

    writer = PdfWriter()
    if not subject.get("no_cover"):
        cover_pdf = _build_cover(subject, submissions)
        cover_reader = PdfReader(cover_pdf)
        writer.add_page(cover_reader.pages[0])

    body_pages = 0
    for submission in submissions:
        upload_path = _stored_upload_path(subject_dir, submission)
        reader = _validate_pdf(upload_path)
        start = _body_start_for_submission(submission, len(reader.pages), len(submissions))
        for page in reader.pages[start:]:
            writer.add_page(page)
            body_pages += 1

    exports_dir = subject_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    export_path = exports_dir / f"{subject['slug']}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.pdf"
    with current_path.open("wb") as output:
        writer.write(output)
    shutil.copy2(current_path, export_path)
    generate_single_page_pdf(current_path)

    return {
        "current_pages": len(writer.pages),
        "body_pages": body_pages,
        "export_path": str(export_path.relative_to(subject_dir)),
        "regenerated": True,
    }


def generate_single_page_pdf(current_path: Path) -> None:
    """Split 2-up landscape pages into individual portrait pages → single.pdf."""
    single_path = current_path.parent / "single.pdf"
    try:
        reader = PdfReader(str(current_path))
    except Exception:
        return
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if w > h:
            mid = w / 2.0
            left = copy.deepcopy(page)
            left.mediabox.left = 0
            left.mediabox.right = mid
            left.mediabox.bottom = 0
            left.mediabox.top = h
            left.cropbox.left = 0
            left.cropbox.right = mid
            left.cropbox.bottom = 0
            left.cropbox.top = h
            writer.add_page(left)
            right = copy.deepcopy(page)
            right.mediabox.left = mid
            right.mediabox.right = w
            right.mediabox.bottom = 0
            right.mediabox.top = h
            right.cropbox.left = mid
            right.cropbox.right = w
            right.cropbox.bottom = 0
            right.cropbox.top = h
            writer.add_page(right)
        else:
            writer.add_page(page)
    if writer.pages:
        with single_path.open("wb") as f:
            writer.write(f)


def _stored_upload_path(subject_dir: Path, submission: dict) -> Path:
    stored = submission.get("stored_upload", "")
    if not stored:
        raise PdfProcessingError("Ein Eintrag hat keine gespeicherte Upload-Datei.")
    path = Path(stored)
    if not path.is_absolute():
        path = subject_dir / path
    if not path.exists():
        raise PdfProcessingError(f"Die gespeicherte Upload-Datei fehlt: {stored}")
    return path


def _validate_pdf(path: Path) -> PdfReader:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise PdfProcessingError("Eine gespeicherte PDF konnte nicht gelesen werden.") from exc
    if len(reader.pages) == 0:
        raise PdfProcessingError("Eine gespeicherte PDF enthält keine Seiten.")
    return reader


def _body_start_for_submission(submission: dict, page_count: int, submission_count: int) -> int:
    if submission.get("collection_import") and submission_count > 1 and page_count > 1:
        return 1
    if submission.get("strip_uploaded_cover") and page_count > 1:
        return 1
    return 0


def _build_cover(subject: dict, entries: list[dict]) -> BytesIO:
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left = 20 * mm
    top = height - 20 * mm

    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 14)
    pdf.drawString(left, top, "Forum")
    pdf.drawString(left, top - 7 * mm, "Wirtschaftsinformatik")
    pdf.drawString(left, top - 14 * mm, "Karlsruher Institut f\u00fcr Technologie")

    pdf.setFont("Helvetica", 9)
    pdf.drawString(left, top - 21 * mm, "Kaiserstra\u00dfe 93, Geb. 05.20, Raum 3C-01  \u2022  76131 Karlsruhe")
    pdf.drawString(left, top - 27 * mm, "Telefon: (0721) 608 - 46879")
    pdf.drawString(left, top - 33 * mm, "Internet: www.forum-wi.de / altklausuren@forum-wi.de")

    logo = _template_logo()
    if logo:
        pdf.drawImage(logo, width - 60 * mm, top - 38 * mm, width=38 * mm, height=38 * mm, mask="auto")

    rule_y = top - 38 * mm
    pdf.setLineWidth(0.8)
    pdf.line(left, rule_y, width - 20 * mm, rule_y)

    title_y = rule_y - 14 * mm
    max_title_width = width - left - 20 * mm
    title_font_size = 30
    pdf.setFont("Helvetica", title_font_size)
    while pdf.stringWidth(subject["title"], "Helvetica", title_font_size) > max_title_width and title_font_size > 14:
        title_font_size -= 1
        pdf.setFont("Helvetica", title_font_size)
    pdf.drawString(left, title_y, subject["title"])
    pdf.setFont("Helvetica", 16)
    pdf.drawString(left, title_y - 10 * mm, "Klausurensammlung")

    footer_y = 18 * mm
    table_y = title_y - 22 * mm
    available_height = table_y - (footer_y + 12 * mm)
    _draw_exam_table(pdf, left + 3 * mm, table_y, entries, available_height=available_height)

    pdf.line(left, footer_y, width - 20 * mm, footer_y)
    pdf.setFont("Helvetica", 12)
    pdf.drawString(left, footer_y - 6 * mm, subject["title"])

    pdf.save()
    buffer.seek(0)
    return buffer


def _draw_exam_table(pdf: canvas.Canvas, x: float, y_top: float, entries: list[dict], available_height: float | None = None) -> None:
    headers = ["Pr\u00fcfungsdatum", "Dozent", "L\u00f6sung"]
    widths = [61 * mm, 64 * mm, 29 * mm]
    normal_row_height = 7.55 * mm
    min_row_height = 4.5 * mm

    n_entries = len(entries)
    # Minimum visual rows even when few entries; expand to fit all entries
    visual_rows = max(13, n_entries)

    if available_height is not None:
        needed = visual_rows + 1  # data rows + header
        ideal_row_height = available_height / needed
        if ideal_row_height < min_row_height:
            # Too many entries to fit at min height; cap how many we show
            row_height = min_row_height
            max_data = int(available_height / row_height) - 1
            visual_rows = min(visual_rows, max(13, max_data))
        else:
            row_height = min(normal_row_height, ideal_row_height)
    else:
        row_height = normal_row_height

    font_size = 9
    header_font_size = 14
    if row_height < 6.5 * mm:
        font_size = 8
        header_font_size = 12
    if row_height < 5.5 * mm:
        font_size = 7
        header_font_size = 10

    rows = visual_rows
    table_width = sum(widths)
    table_height = row_height * (rows + 1)

    pdf.setFillColor(colors.HexColor("#d9d9d9"))
    pdf.rect(x, y_top - row_height, table_width, row_height, fill=1, stroke=0)
    pdf.setFillColor(colors.black)

    pdf.setLineWidth(0.8)
    for index in range(rows + 2):
        y = y_top - index * row_height
        pdf.line(x, y, x + table_width, y)

    current_x = x
    pdf.line(current_x, y_top, current_x, y_top - table_height)
    for width in widths:
        current_x += width
        pdf.line(current_x, y_top, current_x, y_top - table_height)

    pdf.setFont("Helvetica-Bold", header_font_size)
    header_text_y = y_top - row_height * 0.75
    current_x = x + 2 * mm
    for header, width in zip(headers, widths):
        pdf.drawString(current_x, header_text_y, header)
        current_x += width

    pdf.setFont("Helvetica", font_size)
    sorted_entries = sorted(entries, key=lambda entry: tuple(-x for x in _semester_sort_key(entry.get("term", ""))))
    for index, entry in enumerate(sorted_entries[:rows], start=1):
        row_y = y_top - index * row_height - row_height * 0.66
        exam_date = _display_exam_date(entry)
        instructor = entry.get("instructor") or entry.get("dozent") or ""
        solution = _display_solution(entry)
        values = [exam_date, instructor, solution]
        current_x = x + 2 * mm
        for value, width in zip(values, widths):
            pdf.drawString(current_x, row_y, _fit(value, width - 4 * mm, pdf, "Helvetica", font_size))
            current_x += width


def _display_exam_date(entry: dict) -> str:
    if entry.get("exam_date"):
        try:
            return datetime.strptime(entry["exam_date"], "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            return entry["exam_date"]
    return entry.get("term") or ""


def _display_solution(entry: dict) -> str:
    value = (entry.get("solution") or entry.get("has_solution") or "").strip().lower()
    if value in {"yes", "ja", "true", "1", "mit loesung", "mit l\u00f6sung"}:
        return "Ja"
    if value in {"no", "nein", "false", "0", "ohne loesung", "ohne l\u00f6sung"}:
        return "Nein"
    return entry.get("solution") or ""


def _fit(value: str, width: float, pdf: canvas.Canvas, font: str, size: int) -> str:
    text = str(value)
    if pdf.stringWidth(text, font, size) <= width:
        return text
    while text and pdf.stringWidth(f"{text}...", font, size) > width:
        text = text[:-1]
    return f"{text}..." if text else ""


def _template_logo() -> ImageReader | None:
    template_path = Path(__file__).resolve().parent / "Deckblatt_Altklausuren.docx"
    if not template_path.exists():
        return None
    try:
        with zipfile.ZipFile(template_path) as archive:
            image = archive.read("word/media/image1.png")
        return ImageReader(BytesIO(image))
    except Exception:
        return None


def detect_exam_boundaries(pdf_path: Path) -> list[dict]:
    """Analyse each page and propose exam groups based on text patterns."""
    try:
        reader = PdfReader(str(pdf_path))
    except Exception:
        return []

    total = len(reader.pages)
    if total == 0:
        return []

    page_texts = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        page_texts.append(text)

    start_pages = [0]
    for i in range(1, total):
        if _is_likely_exam_start(page_texts[i]):
            start_pages.append(i)

    groups = []
    for idx, start in enumerate(start_pages):
        end = start_pages[idx + 1] - 1 if idx + 1 < len(start_pages) else total - 1
        block_text = " ".join(page_texts[start: end + 1])
        snippet = " ".join(page_texts[start].split())[:200]
        groups.append({
            "start_page": start + 1,
            "end_page": end + 1,
            "semester": _extract_semester(block_text),
            "exam_date": _extract_exam_date(block_text),
            "instructor": "",
            "solution": "unbekannt",
            "kind": "Gedaechtnisprotokoll",
            "notes": "",
            "snippet": snippet,
        })

    return groups


def split_collection(
    *,
    subject: dict,
    subject_dir: Path,
    source_submission: dict,
    groups: list[dict],
) -> list[dict]:
    """Split a collection PDF into individual per-exam PDFs and return submission metadata."""
    source_path = _stored_upload_path(subject_dir, source_submission)
    reader = _validate_pdf(source_path)
    total_pages = len(reader.pages)

    incoming_dir = subject_dir / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, group in enumerate(groups):
        start = max(1, int(group["start_page"]))
        end = min(total_pages, int(group["end_page"]))
        if start > end:
            continue

        writer = PdfWriter()
        for page_num in range(start - 1, end):
            writer.add_page(reader.pages[page_num])

        filename = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-split-{i + 1:02d}.pdf"
        out_path = incoming_dir / filename
        with out_path.open("wb") as f:
            writer.write(f)

        results.append({
            "kind": group.get("kind") or "Gedaechtnisprotokoll",
            "term": group.get("semester", ""),
            "exam_date": group.get("exam_date", ""),
            "instructor": group.get("instructor", ""),
            "solution": group.get("solution", "unbekannt"),
            "notes": group.get("notes", ""),
            "original_filename": filename,
            "stored_upload": str(out_path.relative_to(subject_dir)),
            "added_pages": end - start + 1,
            "existing_body_pages": 0,
            "current_pages": end - start + 1,
            "export_path": "",
            "strip_uploaded_cover": False,
            "collection_import": False,
        })

    return results


def _is_likely_exam_start(text: str) -> bool:
    first = " ".join(text.split())[:300].lower()
    if re.search(r'\bklausur\b', first):
        return True
    if re.search(r'\b(?:wise|sose|ws|ss)\s*\d{2}', first, re.IGNORECASE):
        return True
    return False


def _extract_semester(text: str) -> str:
    match = re.search(
        r'\b((?:WiSe|SoSe|WS|SS)\s*\d{2,4}[/\-]\d{2,4})',
        text, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    match = re.search(r'\b((?:WiSe|SoSe|WS|SS)\s*\d{2,4})\b', text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


def _extract_exam_date(text: str) -> str:
    match = re.search(r'\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b', text)
    if match:
        day, month, year = match.groups()
        year = f"20{year}" if len(year) == 2 else year
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    match = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
    if match:
        return match.group(0)
    return ""


def _semester_sort_key(term: str) -> tuple:
    """Return (year, half) for chronological sorting. SS=0, WS=1.
    E.g. 'WS 24/25' → (2024, 1), 'SS 2025' → (2025, 0).
    Returns (-1, -1) when term is empty/unparseable.
    """
    if not term:
        return (-1, -1)
    t = term.strip()
    # WS / WiSe: "WS 24/25", "WS 2024/25", "WiSe 24/25"
    m = re.match(r'(?:WS|WiSe|Wintersemester)\s*(\d{2,4})[/\-]\d{2,4}', t, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return (year, 1)
    # SS / SoSe: "SS 2025", "SS 25", "SoSe 25"
    m = re.match(r'(?:SS|SoSe|Sommersemester)\s*(\d{2,4})', t, re.IGNORECASE)
    if m:
        year = int(m.group(1))
        if year < 100:
            year += 2000
        return (year, 0)
    return (-1, -1)


def generate_proto_pdf(*, content: str, subject: dict, session: dict, out_path: Path) -> None:
    """Generate a PDF from proto session editor content (plain text, paragraphs preserved)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], fontSize=16, spaceAfter=4)
    meta_style = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, textColor=(0.4, 0.4, 0.4), spaceAfter=12)
    body_style = ParagraphStyle("body", parent=styles["Normal"], fontSize=10, leading=14, spaceAfter=8, alignment=TA_LEFT)

    story = [
        Paragraph(subject["title"], title_style),
        Paragraph(f"Gedächtnisprotokoll · {session['semester']}", meta_style),
    ]

    for para in content.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # Escape HTML-special chars
        para = para.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        para = para.replace("\n", "<br/>")
        story.append(Paragraph(para, body_style))

    doc.build(story)

from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / ".vendor"
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))
sys.path.insert(0, str(ROOT))

from pypdf import PdfReader, PdfWriter

from pdf_workflow import append_submission, _draw_exam_table


def _make_pdf(path: Path, pages: int) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    with path.open("wb") as output:
        writer.write(output)


class PdfWorkflowTest(unittest.TestCase):
    def test_append_submission_replaces_cover_and_appends_body_pages(self):
        with TemporaryDirectory() as temp:
            subject_dir = Path(temp)
            current = subject_dir / "current.pdf"
            upload = subject_dir / "upload.pdf"
            _make_pdf(current, 3)
            _make_pdf(upload, 4)

            subject = {
                "id": "mathe-1",
                "slug": "mathe-1",
                "title": "Mathematik 1",
                "code": "M1",
                "submissions": [{"kind": "Altklausur", "term": "WiSe 2024/25"}],
            }

            result = append_submission(
                subject=subject,
                subject_dir=subject_dir,
                upload_path=upload,
                metadata={"kind": "Gedaechtnisprotokoll", "term": "SoSe 2025"},
                strip_uploaded_cover=True,
            )

            reader = PdfReader(str(current))
            self.assertEqual(len(reader.pages), 6)
            self.assertEqual(result["existing_body_pages"], 2)
            self.assertEqual(result["added_pages"], 3)
            self.assertEqual(result["current_pages"], 6)


    def test_append_submission_new_pages_come_before_existing_body(self):
        with TemporaryDirectory() as temp:
            subject_dir = Path(temp)
            current = subject_dir / "current.pdf"
            upload = subject_dir / "upload.pdf"

            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)  # old cover
            writer.add_blank_page(width=400, height=400)  # old body page 1
            writer.add_blank_page(width=400, height=400)  # old body page 2
            with current.open("wb") as f:
                writer.write(f)

            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)  # new exam page 1
            writer.add_blank_page(width=300, height=300)  # new exam page 2
            with upload.open("wb") as f:
                writer.write(f)

            subject = {
                "id": "bgb-1", "slug": "bgb-1", "title": "BGB", "code": "", "submissions": [],
            }
            append_submission(
                subject=subject,
                subject_dir=subject_dir,
                upload_path=upload,
                metadata={"kind": "Gedaechtnisprotokoll", "term": "WiSe 25/26"},
                strip_uploaded_cover=False,
            )

            reader = PdfReader(str(current))
            # Page 1: new cover (A4)
            # Page 2+3: new exam (normalized to A4) — must come BEFORE old body
            # Page 4+5: old body (width 400)
            self.assertEqual(len(reader.pages), 5)
            self.assertAlmostEqual(float(reader.pages[1].mediabox.width), 595.276, delta=1)
            self.assertAlmostEqual(float(reader.pages[2].mediabox.width), 595.276, delta=1)
            self.assertAlmostEqual(float(reader.pages[3].mediabox.width), 400, delta=1)
            self.assertAlmostEqual(float(reader.pages[4].mediabox.width), 400, delta=1)

    def test_append_submission_respects_no_cover(self):
        with TemporaryDirectory() as temp:
            subject_dir = Path(temp)
            upload = subject_dir / "upload.pdf"
            _make_pdf(upload, 2)

            subject = {
                "id": "oral-protocols",
                "slug": "oral-protocols",
                "title": "Mündliche Protokolle",
                "code": "",
                "no_cover": True,
                "submissions": [],
            }

            result = append_submission(
                subject=subject,
                subject_dir=subject_dir,
                upload_path=upload,
                metadata={"kind": "Gedächtnisprotokoll", "term": "SoSe 2026"},
                strip_uploaded_cover=False,
            )

            reader = PdfReader(str(subject_dir / "current.pdf"))
            self.assertEqual(len(reader.pages), 2)
            self.assertEqual(result["current_pages"], 2)

    def test_cover_table_draws_all_entries(self):
        class RecordingCanvas:
            def __init__(self):
                self.drawn = []

            def setFillColor(self, *_args):
                pass

            def rect(self, *_args, **_kwargs):
                pass

            def setLineWidth(self, *_args):
                pass

            def line(self, *_args):
                pass

            def setFont(self, *_args):
                pass

            def drawString(self, _x, _y, text):
                self.drawn.append(text)

            def stringWidth(self, text, _font, size):
                return len(str(text)) * size * 0.5

        pdf = RecordingCanvas()
        start = date(2026, 1, 1)
        dates = [start + timedelta(days=index) for index in range(45)]
        entries = [
            {"exam_date": item.isoformat(), "instructor": f"Dozent {index}", "solution": "Ja"}
            for index, item in enumerate(dates, start=1)
        ]

        _draw_exam_table(pdf, 0, 500, entries, available_height=200)

        for item in dates:
            self.assertIn(item.strftime("%d.%m.%Y"), pdf.drawn)


if __name__ == "__main__":
    unittest.main()

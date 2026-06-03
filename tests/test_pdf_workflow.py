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

from pdf_workflow import PdfProcessingError, append_submission, generate_single_page_pdf, regenerate_current_pdf, _draw_exam_table, _ensure_a4_reader


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

    def test_preserved_collection_import_generates_single_pdf(self):
        with TemporaryDirectory() as temp:
            subject_dir = Path(temp)
            incoming = subject_dir / "incoming"
            incoming.mkdir()
            upload = incoming / "druck.pdf"
            _make_pdf(upload, 2)

            subject = {
                "id": "mathe-1",
                "slug": "mathe-1",
                "title": "Mathematik 1",
                "code": "M1",
                "submissions": [
                    {
                        "kind": "Sammlungsimport",
                        "stored_upload": "incoming/druck.pdf",
                        "collection_import": True,
                    }
                ],
            }

            regenerate_current_pdf(subject=subject, subject_dir=subject_dir)

            self.assertTrue((subject_dir / "single.pdf").exists())

    def test_stored_upload_rejects_absolute_or_escaping_paths(self):
        with TemporaryDirectory() as temp:
            subject_dir = Path(temp) / "subject"
            subject_dir.mkdir()
            outside = Path(temp) / "outside.pdf"
            _make_pdf(outside, 1)
            subject = {
                "id": "mathe-1",
                "slug": "mathe-1",
                "title": "Mathematik 1",
                "code": "M1",
                "submissions": [
                    {
                        "kind": "Altklausur",
                        "stored_upload": str(outside),
                    }
                ],
            }

            with self.assertRaises(PdfProcessingError):
                regenerate_current_pdf(subject=subject, subject_dir=subject_dir)

            subject["submissions"][0]["stored_upload"] = "../outside.pdf"
            with self.assertRaises(PdfProcessingError):
                regenerate_current_pdf(subject=subject, subject_dir=subject_dir)

    def test_non_a4_normalization_is_cached_next_to_upload(self):
        with TemporaryDirectory() as temp:
            source = Path(temp) / "incoming" / "scan.pdf"
            source.parent.mkdir()
            writer = PdfWriter()
            writer.add_blank_page(width=421, height=595)
            with source.open("wb") as output:
                writer.write(output)

            first = _ensure_a4_reader(source)
            second = _ensure_a4_reader(source)
            cache_files = list((source.parent / ".normalized_a4").glob("*.pdf"))

            self.assertEqual(len(first.pages), 1)
            self.assertEqual(len(second.pages), 1)
            self.assertEqual(len(cache_files), 1)

    def test_single_page_pdf_copies_portrait_pdf_without_rewriting(self):
        with TemporaryDirectory() as temp:
            current = Path(temp) / "current.pdf"
            _make_pdf(current, 2)
            original_bytes = current.read_bytes()

            generate_single_page_pdf(current)

            self.assertEqual((Path(temp) / "single.pdf").read_bytes(), original_bytes)

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

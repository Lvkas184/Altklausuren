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

from pdf_workflow import append_submission


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


if __name__ == "__main__":
    unittest.main()

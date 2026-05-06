from __future__ import annotations

import json
import re
import secrets
from datetime import datetime
from pathlib import Path


class Catalog:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.catalog_path = data_dir / "catalog.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def list_subjects(self) -> list[dict]:
        data = self._read()
        subjects = list(data["subjects"].values())
        return sorted(subjects, key=lambda item: item["title"].lower())

    def get_subject(self, subject_id: str) -> dict | None:
        return self._read()["subjects"].get(subject_id)

    def find_or_create_subject(self, title: str, code: str = "") -> dict:
        data = self._read()
        for subject in data["subjects"].values():
            if subject["title"].casefold() == title.casefold():
                return subject
        return self.create_subject(title=title, code=code)

    def create_subject(self, title: str, code: str = "") -> dict:
        data = self._read()
        slug = _slugify(code or title)
        subject_id = slug
        if subject_id in data["subjects"]:
            subject_id = f"{slug}-{secrets.token_hex(2)}"

        subject = {
            "id": subject_id,
            "slug": subject_id,
            "title": title,
            "code": code,
            "submissions": [],
            "created_at": _now(),
            "updated_at": _now(),
            "current_pages": 0,
        }
        data["subjects"][subject_id] = subject
        self.subject_dir(subject_id).mkdir(parents=True, exist_ok=True)
        self._write(data)
        return subject

    def add_submission(self, subject_id: str, submission: dict) -> None:
        data = self._read()
        subject = data["subjects"][subject_id]
        subject["submissions"].append(submission | {"added_at": _now()})
        subject["updated_at"] = _now()
        subject["current_pages"] = submission.get("current_pages", subject.get("current_pages", 0))
        self._write(data)

    def update_drive_subject(self, subject_id: str, drive_metadata: dict, current_pages: int) -> None:
        data = self._read()
        subject = data["subjects"][subject_id]
        subject["drive"] = drive_metadata
        subject["updated_at"] = _now()
        subject["current_pages"] = current_pages
        self._write(data)

    def subject_dir(self, subject_id: str) -> Path:
        return self.data_dir / "subjects" / subject_id

    def _read(self) -> dict:
        if not self.catalog_path.exists():
            return {"subjects": {}}
        return json.loads(self.catalog_path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        self.catalog_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or f"fach-{secrets.token_hex(2)}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")

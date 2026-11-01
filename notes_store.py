"""The user's own saved analysis notes, kept in a small JSON file next to the app.

Qt-free. Notes are the user's opinions, so the assistant treats them as
"your notes" rather than as verified data.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

NOTES_FILE = Path(__file__).resolve().parent / "saved_analysis.json"


def _read(path: Path) -> list:
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return []
    return [n for n in data if isinstance(n, dict) and n.get("text")] if isinstance(data, list) else []


def _write(notes: list, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(notes, file, indent=2)
    tmp.replace(path)          # atomic on the same volume: never leaves a half-written file


def load_notes(ticker: str | None = None, path: Path = NOTES_FILE) -> list:
    notes = _read(path)
    if ticker:
        notes = [n for n in notes if n.get("ticker") == ticker.upper()]
    return sorted(notes, key=lambda n: n.get("created", 0), reverse=True)


def add_note(ticker: str, text: str, title: str = "", path: Path = NOTES_FILE) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("A note cannot be empty.")
    note = {"id": uuid.uuid4().hex[:8], "ticker": ticker.upper(), "title": title.strip(),
            "text": text, "created": time.time()}
    notes = _read(path)
    notes.append(note)
    _write(notes, path)
    return note


def delete_note(note_id: str, path: Path = NOTES_FILE) -> bool:
    notes = _read(path)
    kept = [n for n in notes if n.get("id") != note_id]
    if len(kept) == len(notes):
        return False
    _write(kept, path)
    return True

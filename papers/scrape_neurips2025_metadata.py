#!/usr/bin/env python3
"""Build a NeurIPS 2025 accepted-paper metadata workbook from OpenReview."""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scrape_iclr2026_metadata as openreview_helpers  # noqa: E402


API_BASE = "https://api2.openreview.net/notes"
VENUE_ID = "NeurIPS.cc/2025/Conference"
ACCEPTED_VENUES = {"NeurIPS 2025 poster", "NeurIPS 2025 spotlight", "NeurIPS 2025 oral"}
DEFAULT_OUTDIR = Path("papers/neurips2025")


def stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def fetch_notes(cache_path: Path, refresh: bool, sleep_seconds: float) -> list[dict[str, Any]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    session = requests.Session()
    session.headers.update({"User-Agent": "train-llm-neurips2025-scraper/0.1"})
    notes: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000
    while True:
        response = session.get(
            API_BASE,
            params={"content.venueid": VENUE_ID, "limit": page_size, "offset": offset},
            timeout=60,
        )
        if response.status_code == 429:
            print(f"rate_limited offset={offset}; sleeping 15s")
            time.sleep(15)
            continue
        response.raise_for_status()
        batch = response.json().get("notes", [])
        print(f"fetched offset={offset} notes={len(batch)}")
        if not batch:
            break
        notes.extend(batch)
        offset += len(batch)
        if len(batch) < page_size:
            break
        time.sleep(sleep_seconds)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return notes


def normalize_notes(notes: list[dict[str, Any]], fetched_at: str) -> list[dict[str, str]]:
    previous_venue_id = openreview_helpers.VENUE_ID
    try:
        openreview_helpers.VENUE_ID = VENUE_ID
        rows = []
        for note in notes:
            venue = stringify(openreview_helpers.get_value(note.get("content", {}), "venue"))
            if venue in ACCEPTED_VENUES:
                rows.append(openreview_helpers.note_to_row(note, fetched_at))
        rows.sort(key=lambda row: (row["venue"], int(row["submission_number"] or 0), row["title"]))
        return rows
    finally:
        openreview_helpers.VENUE_ID = previous_venue_id


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx_cell(row_idx: int, col_idx: int, value: Any) -> str:
    ref = f"{col_name(col_idx)}{row_idx}"
    text = stringify(value)
    if len(text) > 32767:
        text = text[:32760] + " [TRUNC]"
    return f'<c r="{ref}" t="inlineStr"><is><t>{html.escape(text, quote=True)}</t></is></c>'


def xlsx_sheet(rows: list[list[Any]]) -> str:
    body = []
    for row_idx, row in enumerate(rows, start=1):
        body.append(f'<row r="{row_idx}">{"".join(xlsx_cell(row_idx, col_idx, value) for col_idx, value in enumerate(row, start=1))}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(body)}</sheetData>'
        '</worksheet>'
    )


def write_xlsx(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    venue_counts = Counter(row["venue"] for row in rows)
    summary_rows = [
        ["metric", "value"],
        ["generated_at_utc", datetime.now(timezone.utc).isoformat()],
        ["venue_id", VENUE_ID],
        ["accepted_rows", len(rows)],
        ["venue_counts", json.dumps(venue_counts, ensure_ascii=False)],
        ["rows_with_supplementary_material", sum(1 for row in rows if row["has_supplementary_material"] == "yes")],
        ["rows_with_github_urls_in_metadata_text", sum(1 for row in rows if row["github_urls"])],
        ["rows_with_repository_urls_in_metadata_text", sum(1 for row in rows if row["repository_urls"])],
        ["rows_with_dataset_urls_in_metadata_text", sum(1 for row in rows if row["dataset_urls"])],
        ["rows_with_arxiv_urls_in_metadata_text", sum(1 for row in rows if row["arxiv_ids"])],
        ["note", "GitHub, dataset, and arXiv links are extracted from OpenReview text metadata fields, not from full PDF body text."],
    ]
    sheet_rows = [fieldnames] + [[row.get(field, "") for field in fieldnames] for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Papers" sheetId="1" r:id="rId1"/><sheet name="Summary" sheetId="2" r:id="rId2"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", xlsx_sheet(sheet_rows))
        zf.writestr("xl/worksheets/sheet2.xml", xlsx_sheet(summary_rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape NeurIPS 2025 accepted metadata from OpenReview.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--sleep", type=float, default=1.5)
    args = parser.parse_args()

    outdir = Path(args.outdir)
    cache_path = outdir / "openreview_neurips2025_notes_raw.json"
    notes = fetch_notes(cache_path, args.refresh, args.sleep)
    rows = normalize_notes(notes, datetime.now(timezone.utc).isoformat())
    if not rows:
        raise RuntimeError("No NeurIPS 2025 accepted rows found.")
    fieldnames = list(rows[0].keys())
    csv_path = outdir / "neurips2025_accepted_papers_metadata.csv"
    jsonl_path = outdir / "neurips2025_accepted_papers_metadata.jsonl"
    xlsx_path = outdir / "neurips2025_accepted_papers_metadata.xlsx"
    write_csv(csv_path, rows, fieldnames)
    write_jsonl(jsonl_path, rows)
    write_xlsx(xlsx_path, rows, fieldnames)
    print(f"raw_notes={len(notes)}")
    print(f"accepted_rows={len(rows)}")
    for venue, count in Counter(row["venue"] for row in rows).most_common():
        print(f"{venue}={count}")
    print(f"csv={csv_path}")
    print(f"jsonl={jsonl_path}")
    print(f"xlsx={xlsx_path}")
    print(f"raw_cache={cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

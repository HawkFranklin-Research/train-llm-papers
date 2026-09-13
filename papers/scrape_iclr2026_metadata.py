#!/usr/bin/env python3
"""Build an ICLR 2026 accepted-paper metadata workbook from OpenReview.

The OpenReview page itself is JavaScript-rendered, but the public API exposes
accepted notes. This script fetches notes whose venue label is one of:

- ICLR 2026 Poster
- ICLR 2026 Oral

It writes CSV, JSONL, raw API cache JSON, and a minimal XLSX workbook.
The XLSX writer is implemented with the Python standard library so the script
does not require openpyxl/xlsxwriter.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import re
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import requests


API_BASE = "https://api2.openreview.net/notes"
OPENREVIEW_BASE = "https://openreview.net"
VENUE_ID = "ICLR.cc/2026/Conference"
ACCEPTED_VENUES = {"ICLR 2026 Poster", "ICLR 2026 Oral"}
DEFAULT_OUTDIR = Path("papers/iclr2026")
REQUEST_TIMEOUT = 60

URL_RE = re.compile(r"https?://[^\s\]\[(){}<>\"']+")
ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}(?:v\d+)?)", re.I)
GITHUB_RE = re.compile(r"https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?", re.I)
REPO_RE = re.compile(
    r"https?://(?:github\.com|gitlab\.com|bitbucket\.org|anonymous\.4open\.science|codeberg\.org)/[^\s\]\[(){}<>\"']+",
    re.I,
)
DATASET_HOSTS = (
    "huggingface.co/datasets",
    "kaggle.com",
    "zenodo.org",
    "figshare.com",
    "dataverse",
    "archive.ics.uci.edu",
    "openml.org",
    "paperswithcode.com/dataset",
)


def get_value(content: dict[str, Any], key: str, default: Any = "") -> Any:
    value = content.get(key, default)
    if isinstance(value, dict) and "value" in value:
        return value["value"]
    return value


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def clean_url(url: str) -> str:
    return url.rstrip(".,;:)")


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        item = clean_url(item.strip())
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def extract_urls(*values: Any) -> list[str]:
    text = "\n".join(stringify(value) for value in values if value)
    return unique(match.group(0) for match in URL_RE.finditer(text))


def extract_arxiv_ids(urls: Iterable[str]) -> list[str]:
    ids: list[str] = []
    for url in urls:
        match = ARXIV_RE.search(url)
        if match:
            ids.append(match.group(1).removesuffix(".pdf"))
    return unique(ids)


def classify_urls(urls: list[str]) -> dict[str, list[str]]:
    github_urls = unique(match.group(0).rstrip("/") for url in urls for match in GITHUB_RE.finditer(url))
    repo_urls = unique(match.group(0).rstrip("/") for url in urls for match in REPO_RE.finditer(url))
    dataset_urls = unique(url for url in urls if any(host in url.lower() for host in DATASET_HOSTS))
    arxiv_ids = extract_arxiv_ids(urls)
    arxiv_abs_urls = [f"https://arxiv.org/abs/{arxiv_id}" for arxiv_id in arxiv_ids]
    arxiv_pdf_urls = [f"https://arxiv.org/pdf/{arxiv_id}.pdf" for arxiv_id in arxiv_ids]
    arxiv_source_urls = [f"https://arxiv.org/e-print/{arxiv_id}" for arxiv_id in arxiv_ids]
    return {
        "github_urls": github_urls,
        "repository_urls": repo_urls,
        "dataset_urls": dataset_urls,
        "arxiv_ids": arxiv_ids,
        "arxiv_abs_urls": arxiv_abs_urls,
        "arxiv_pdf_urls": arxiv_pdf_urls,
        "arxiv_source_urls": arxiv_source_urls,
    }


def absolute_openreview_url(path_or_url: str) -> str:
    if not path_or_url:
        return ""
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url
    if not path_or_url.startswith("/"):
        path_or_url = "/" + path_or_url
    return OPENREVIEW_BASE + path_or_url


def fetch_notes(cache_path: Path, refresh: bool, sleep_seconds: float, limit: int) -> list[dict[str, Any]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    session = requests.Session()
    session.headers.update({"User-Agent": "train-llm-metadata-scraper/0.1"})
    notes: list[dict[str, Any]] = []
    offset = 0
    page_size = 1000

    while True:
        params = {
            "content.venueid": VENUE_ID,
            "limit": page_size,
            "offset": offset,
        }
        response = session.get(API_BASE, params=params, timeout=REQUEST_TIMEOUT)
        if response.status_code == 429:
            wait = max(10.0, sleep_seconds * 5)
            print(f"rate_limited offset={offset}; sleeping {wait:.1f}s")
            time.sleep(wait)
            continue
        response.raise_for_status()
        batch = response.json().get("notes", [])
        print(f"fetched offset={offset} notes={len(batch)}")
        if not batch:
            break
        notes.extend(batch)
        offset += len(batch)
        if limit and len(notes) >= limit:
            notes = notes[:limit]
            break
        if len(batch) < page_size:
            break
        time.sleep(sleep_seconds)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return notes


def note_to_row(note: dict[str, Any], fetched_at: str) -> dict[str, str]:
    content = note.get("content", {})
    note_id = note.get("id", "")
    forum_id = note.get("forum", note_id)
    pdf_path = stringify(get_value(content, "pdf"))
    supplement_path = stringify(get_value(content, "supplementary_material"))
    title = stringify(get_value(content, "title"))
    authors = stringify(get_value(content, "authors"))
    authorids = stringify(get_value(content, "authorids"))
    keywords = stringify(get_value(content, "keywords"))
    abstract = stringify(get_value(content, "abstract"))
    tldr = stringify(get_value(content, "TLDR"))
    bibtex = stringify(get_value(content, "_bibtex"))
    venue = stringify(get_value(content, "venue"))
    primary_area = stringify(get_value(content, "primary_area"))
    paperhash = stringify(get_value(content, "paperhash"))

    all_urls = extract_urls(title, authors, keywords, abstract, tldr, bibtex)
    classes = classify_urls(all_urls)
    official_forum_url = f"{OPENREVIEW_BASE}/forum?id={forum_id}"
    official_pdf_url = f"{OPENREVIEW_BASE}/pdf?id={note_id}"
    content_pdf_url = absolute_openreview_url(pdf_path)
    supplement_url = absolute_openreview_url(supplement_path)

    return {
        "openreview_id": note_id,
        "forum_id": forum_id,
        "submission_number": stringify(note.get("number", "")),
        "venue": venue,
        "accepted_type": venue.replace("ICLR 2026 ", ""),
        "title": title,
        "authors": authors,
        "authorids": authorids,
        "primary_area": primary_area,
        "keywords": keywords,
        "tldr": tldr,
        "abstract": abstract,
        "openreview_forum_url": official_forum_url,
        "official_pdf_download_url": official_pdf_url,
        "content_pdf_url": content_pdf_url,
        "supplementary_material_url": supplement_url,
        "supplementary_material_type": Path(supplement_path).suffix.lower().lstrip("."),
        "has_supplementary_material": "yes" if supplement_url else "no",
        "github_urls": "; ".join(classes["github_urls"]),
        "repository_urls": "; ".join(classes["repository_urls"]),
        "dataset_urls": "; ".join(classes["dataset_urls"]),
        "arxiv_ids": "; ".join(classes["arxiv_ids"]),
        "arxiv_abs_urls": "; ".join(classes["arxiv_abs_urls"]),
        "arxiv_pdf_urls": "; ".join(classes["arxiv_pdf_urls"]),
        "arxiv_source_urls": "; ".join(classes["arxiv_source_urls"]),
        "all_extracted_urls": "; ".join(all_urls),
        "paperhash": paperhash,
        "bibtex": bibtex,
        "license": stringify(note.get("license", "")),
        "cdate": stringify(note.get("cdate", "")),
        "pdate": stringify(note.get("pdate", "")),
        "mdate": stringify(note.get("mdate", "")),
        "fetched_at_utc": fetched_at,
    }


def normalize_notes(notes: list[dict[str, Any]], fetched_at: str) -> list[dict[str, str]]:
    rows = []
    for note in notes:
        venue = stringify(get_value(note.get("content", {}), "venue"))
        if venue in ACCEPTED_VENUES:
            rows.append(note_to_row(note, fetched_at))
    rows.sort(key=lambda row: (row["accepted_type"], int(row["submission_number"] or 0), row["title"]))
    return rows


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
    text = html.escape(text, quote=True)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


def xlsx_sheet(rows: list[list[Any]]) -> str:
    row_xml = []
    for r_idx, row in enumerate(rows, start=1):
        cells = "".join(xlsx_cell(r_idx, c_idx, value) for c_idx, value in enumerate(row, start=1))
        row_xml.append(f'<row r="{r_idx}">{cells}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        '</worksheet>'
    )


def write_xlsx(path: Path, papers_rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    venue_counts = Counter(row["venue"] for row in papers_rows)
    supplement_count = sum(1 for row in papers_rows if row["has_supplementary_material"] == "yes")
    github_count = sum(1 for row in papers_rows if row["github_urls"])
    repo_count = sum(1 for row in papers_rows if row["repository_urls"])
    dataset_count = sum(1 for row in papers_rows if row["dataset_urls"])
    arxiv_count = sum(1 for row in papers_rows if row["arxiv_ids"])

    summary_rows = [
        ["metric", "value"],
        ["generated_at_utc", datetime.now(timezone.utc).isoformat()],
        ["venue_id", VENUE_ID],
        ["accepted_rows", len(papers_rows)],
        ["poster_rows", venue_counts.get("ICLR 2026 Poster", 0)],
        ["oral_rows", venue_counts.get("ICLR 2026 Oral", 0)],
        ["rows_with_supplementary_material", supplement_count],
        ["rows_with_github_urls_in_metadata_text", github_count],
        ["rows_with_repository_urls_in_metadata_text", repo_count],
        ["rows_with_dataset_urls_in_metadata_text", dataset_count],
        ["rows_with_arxiv_urls_in_metadata_text", arxiv_count],
        ["note", "GitHub, dataset, and arXiv links are extracted from OpenReview text metadata fields, not from full PDF body text."],
    ]
    paper_sheet_rows = [fieldnames] + [[row.get(field, "") for field in fieldnames] for row in papers_rows]

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '</Types>',
        )
        zf.writestr(
            "_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
        )
        zf.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets>'
            '<sheet name="Papers" sheetId="1" r:id="rId1"/>'
            '<sheet name="Summary" sheetId="2" r:id="rId2"/>'
            '</sheets>'
            '</workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>'
            '</Relationships>',
        )
        zf.writestr("xl/worksheets/sheet1.xml", xlsx_sheet(paper_sheet_rows))
        zf.writestr("xl/worksheets/sheet2.xml", xlsx_sheet(summary_rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape ICLR 2026 accepted metadata from OpenReview.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Output directory.")
    parser.add_argument("--refresh", action="store_true", help="Refetch OpenReview notes instead of using cache.")
    parser.add_argument("--sleep", type=float, default=1.5, help="Sleep between paginated API calls.")
    parser.add_argument("--limit", type=int, default=0, help="Debug limit; 0 means all notes.")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    cache_path = outdir / "openreview_iclr2026_notes_raw.json"
    fetched_at = datetime.now(timezone.utc).isoformat()
    notes = fetch_notes(cache_path, refresh=args.refresh, sleep_seconds=args.sleep, limit=args.limit)
    rows = normalize_notes(notes, fetched_at=fetched_at)

    if not rows:
        raise RuntimeError("No accepted ICLR 2026 rows found.")

    fieldnames = list(rows[0].keys())
    csv_path = outdir / "iclr2026_accepted_papers_metadata.csv"
    jsonl_path = outdir / "iclr2026_accepted_papers_metadata.jsonl"
    xlsx_path = outdir / "iclr2026_accepted_papers_metadata.xlsx"

    write_csv(csv_path, rows, fieldnames)
    write_jsonl(jsonl_path, rows)
    write_xlsx(xlsx_path, rows, fieldnames)

    venue_counts = Counter(row["venue"] for row in rows)
    print(f"raw_notes={len(notes)}")
    print(f"accepted_rows={len(rows)}")
    for venue, count in venue_counts.most_common():
        print(f"{venue}={count}")
    print(f"csv={csv_path}")
    print(f"jsonl={jsonl_path}")
    print(f"xlsx={xlsx_path}")
    print(f"raw_cache={cache_path}")
    print("link_extraction_note=GitHub/dataset/arXiv links are extracted from OpenReview text metadata, not full PDF body text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

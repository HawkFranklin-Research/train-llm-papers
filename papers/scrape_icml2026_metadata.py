#!/usr/bin/env python3
"""Build an ICML 2026 accepted-paper metadata workbook.

Source: ICML's public virtual site JSON:

- /static/virtual/data/icml-2026-orals-posters.json
- /static/virtual/data/icml-2026-abstracts.json

The JSON provides title, authors, institutions, decision, topic, session,
schedule, virtual poster URL, and OpenReview forum URL. OpenReview's direct
note API for these forum IDs is not publicly readable from this environment,
so PDF links are constructed as https://openreview.net/pdf?id=<forum_id>.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, parse_qs

import requests


DEFAULT_OUTDIR = Path("papers/icml2026")
BASE = "https://icml.cc"
PAPERS_JSON_URL = BASE + "/static/virtual/data/icml-2026-orals-posters.json"
ABSTRACTS_JSON_URL = BASE + "/static/virtual/data/icml-2026-abstracts.json"
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


def stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(stringify(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def clean_url(url: str) -> str:
    return url.strip().rstrip(".,;:)")


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        item = clean_url(item)
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def extract_urls(*values: Any) -> list[str]:
    text = "\n".join(stringify(value) for value in values if value)
    return unique(match.group(0) for match in URL_RE.finditer(text))


def extract_arxiv_ids(urls: Iterable[str]) -> list[str]:
    ids = []
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
    return {
        "github_urls": github_urls,
        "repository_urls": repo_urls,
        "dataset_urls": dataset_urls,
        "arxiv_ids": arxiv_ids,
        "arxiv_abs_urls": [f"https://arxiv.org/abs/{arxiv_id}" for arxiv_id in arxiv_ids],
        "arxiv_pdf_urls": [f"https://arxiv.org/pdf/{arxiv_id}.pdf" for arxiv_id in arxiv_ids],
        "arxiv_source_urls": [f"https://arxiv.org/e-print/{arxiv_id}" for arxiv_id in arxiv_ids],
    }


def fetch_json(url: str) -> Any:
    response = requests.get(url, timeout=90, headers={"User-Agent": "train-llm-icml2026-scraper/0.1"})
    response.raise_for_status()
    return response.json()


def openreview_id(paper_url: str) -> str:
    if not paper_url:
        return ""
    parsed = urlparse(paper_url)
    return parse_qs(parsed.query).get("id", [""])[0]


def media_urls(record: dict[str, Any]) -> list[str]:
    urls = []
    for media in record.get("eventmedia", []) or []:
        uri = media.get("uri")
        if uri:
            urls.append(uri)
    return unique(urls)


def author_fields(authors: list[dict[str, Any]]) -> tuple[str, str]:
    names = []
    institutions = []
    for author in authors:
        if author.get("fullname"):
            names.append(author["fullname"])
        if author.get("institution"):
            institutions.append(author["institution"])
    return "; ".join(names), "; ".join(unique(institutions))


def record_to_row(record: dict[str, Any], abstracts: dict[str, str], fetched_at: str) -> dict[str, str]:
    event_id = str(record.get("id", ""))
    abstract = abstracts.get(event_id, "")
    authors, institutions = author_fields(record.get("authors", []) or [])
    all_urls = extract_urls(record.get("name"), abstract, media_urls(record))
    classes = classify_urls(all_urls)
    forum_id = openreview_id(record.get("paper_url", ""))
    openreview_forum = record.get("paper_url", "") or (f"https://openreview.net/forum?id={forum_id}" if forum_id else "")
    official_pdf = record.get("paper_pdf_url") or (f"https://openreview.net/pdf?id={forum_id}" if forum_id else "")
    return {
        "icml_event_id": event_id,
        "openreview_id": forum_id,
        "title": stringify(record.get("name", "")),
        "authors": authors,
        "author_institutions": institutions,
        "decision": stringify(record.get("decision", "")),
        "event_type": stringify(record.get("event_type") or record.get("eventtype", "")),
        "topic": stringify(record.get("topic", "")),
        "keywords": stringify(record.get("keywords", "")),
        "session": stringify(record.get("session", "")),
        "room_name": stringify(record.get("room_name", "")),
        "starttime": stringify(record.get("starttime", "")),
        "endtime": stringify(record.get("endtime", "")),
        "virtualsite_url": urljoin(BASE, stringify(record.get("virtualsite_url", ""))),
        "openreview_forum_url": openreview_forum,
        "official_pdf_download_url": official_pdf,
        "supplementary_material_url": "",
        "source_archive_url": "",
        "github_urls": "; ".join(classes["github_urls"]),
        "repository_urls": "; ".join(classes["repository_urls"]),
        "dataset_urls": "; ".join(classes["dataset_urls"]),
        "arxiv_ids": "; ".join(classes["arxiv_ids"]),
        "arxiv_abs_urls": "; ".join(classes["arxiv_abs_urls"]),
        "arxiv_pdf_urls": "; ".join(classes["arxiv_pdf_urls"]),
        "arxiv_source_urls": "; ".join(classes["arxiv_source_urls"]),
        "all_extracted_urls": "; ".join(all_urls),
        "abstract": abstract,
        "sourceurl": stringify(record.get("sourceurl", "")),
        "parent_id": stringify(record.get("parent_id", "")),
        "visible": stringify(record.get("visible", "")),
        "fetched_at_utc": fetched_at,
    }


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
    decisions = Counter(row["decision"] for row in rows)
    event_types = Counter(row["event_type"] for row in rows)
    summary_rows = [
        ["metric", "value"],
        ["generated_at_utc", datetime.now(timezone.utc).isoformat()],
        ["source", "ICML virtual site JSON"],
        ["rows", len(rows)],
        ["decisions", json.dumps(decisions, ensure_ascii=False)],
        ["event_types", json.dumps(event_types, ensure_ascii=False)],
        ["rows_with_github_urls", sum(1 for row in rows if row["github_urls"])],
        ["rows_with_repository_urls", sum(1 for row in rows if row["repository_urls"])],
        ["rows_with_dataset_urls", sum(1 for row in rows if row["dataset_urls"])],
        ["rows_with_arxiv_urls", sum(1 for row in rows if row["arxiv_ids"])],
        ["note", "PDF URLs are constructed from OpenReview forum IDs when direct PDF URLs are not present in ICML JSON."],
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
    parser = argparse.ArgumentParser(description="Scrape ICML 2026 metadata from ICML virtual-site JSON.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    outdir = Path(args.outdir)
    papers_cache = outdir / "icml2026_orals_posters_raw.json"
    abstracts_cache = outdir / "icml2026_abstracts_raw.json"

    if papers_cache.exists() and abstracts_cache.exists() and not args.refresh:
        papers_data = json.loads(papers_cache.read_text(encoding="utf-8"))
        abstracts = json.loads(abstracts_cache.read_text(encoding="utf-8"))
    else:
        papers_data = fetch_json(PAPERS_JSON_URL)
        abstracts = fetch_json(ABSTRACTS_JSON_URL)
        outdir.mkdir(parents=True, exist_ok=True)
        papers_cache.write_text(json.dumps(papers_data, ensure_ascii=False, indent=2), encoding="utf-8")
        abstracts_cache.write_text(json.dumps(abstracts, ensure_ascii=False, indent=2), encoding="utf-8")

    fetched_at = datetime.now(timezone.utc).isoformat()
    records = papers_data.get("results", [])
    rows = [record_to_row(record, abstracts, fetched_at) for record in records]
    rows.sort(key=lambda row: (row["decision"], row["event_type"], row["title"]))
    fieldnames = list(rows[0].keys())
    csv_path = outdir / "icml2026_papers_metadata.csv"
    jsonl_path = outdir / "icml2026_papers_metadata.jsonl"
    xlsx_path = outdir / "icml2026_papers_metadata.xlsx"
    write_csv(csv_path, rows, fieldnames)
    write_jsonl(jsonl_path, rows)
    write_xlsx(xlsx_path, rows, fieldnames)
    print(f"rows={len(rows)}")
    print(f"csv={csv_path}")
    print(f"jsonl={jsonl_path}")
    print(f"xlsx={xlsx_path}")
    print(f"raw_papers={papers_cache}")
    print(f"raw_abstracts={abstracts_cache}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

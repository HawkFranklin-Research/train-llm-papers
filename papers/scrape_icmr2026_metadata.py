#!/usr/bin/env python3
"""Build an ACM ICMR 2026 proceedings metadata workbook.

Source: Crossref records for ACM proceedings DOI 10.1145/3805622.

ACM DL is the canonical paper host, but it is protected from this environment
by Cloudflare. Crossref exposes the public DOI metadata, so this script writes
the official DOI/ACM URL/PDF-target metadata and best-effort URL extraction
from Crossref text fields. Code, dataset, arXiv, and source-archive columns are
included, but Crossref usually does not contain those links.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests


CROSSREF_WORKS = "https://api.crossref.org/works"
PROCEEDINGS_DOI = "10.1145/3805622"
CONTAINER_TITLE = "Proceedings of the 2026 International Conference on Multimedia Retrieval"
DEFAULT_OUTDIR = Path("papers/icmr2026")
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


def date_parts(record: dict[str, Any], key: str) -> str:
    parts = record.get(key, {}).get("date-parts", [])
    if not parts:
        return ""
    return "-".join(f"{int(part):02d}" for part in parts[0])


def author_name(author: dict[str, Any]) -> str:
    given = author.get("given", "")
    family = author.get("family", "")
    return " ".join(part for part in [given, family] if part).strip()


def author_affiliations(author: dict[str, Any]) -> str:
    affiliations = author.get("affiliation", [])
    return "; ".join(aff.get("name", "") for aff in affiliations if aff.get("name"))


def fetch_crossref(cache_path: Path, refresh: bool) -> list[dict[str, Any]]:
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    session = requests.Session()
    session.headers.update({"User-Agent": "train-llm-icmr2026-scraper/0.1"})
    params = {
        "filter": "prefix:10.1145",
        "query": CONTAINER_TITLE,
        "rows": 1000,
    }
    response = session.get(CROSSREF_WORKS, params=params, timeout=60)
    response.raise_for_status()
    items = response.json()["message"]["items"]
    records = [
        item
        for item in items
        if item.get("DOI", "").startswith(PROCEEDINGS_DOI + ".")
        or CONTAINER_TITLE in item.get("container-title", [])
    ]
    records.sort(key=lambda item: (int(str(item.get("page", "0")).split("-", 1)[0] or 0), item.get("DOI", "")))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    return records


def record_to_row(record: dict[str, Any], fetched_at: str) -> dict[str, str]:
    doi = record.get("DOI", "")
    title = stringify(record.get("title", []))
    authors = record.get("author", [])
    author_names = "; ".join(author_name(author) for author in authors if author_name(author))
    affiliations = unique(author_affiliations(author) for author in authors if author_affiliations(author))
    urls = extract_urls(title, record.get("abstract", ""))
    classes = classify_urls(urls)
    doi_url = record.get("URL") or f"https://doi.org/{doi}"
    acm_url = record.get("resource", {}).get("primary", {}).get("URL") or f"https://dl.acm.org/doi/{doi}"
    pdf_url = f"https://dl.acm.org/doi/pdf/{doi}" if doi else ""
    return {
        "doi": doi,
        "title": title,
        "authors": author_names,
        "author_affiliations": "; ".join(affiliations),
        "venue": "ICMR 2026",
        "container_title": stringify(record.get("container-title", [])),
        "event_name": record.get("event", {}).get("name", ""),
        "event_location": record.get("event", {}).get("location", ""),
        "pages": stringify(record.get("page", "")),
        "published_print": date_parts(record, "published-print"),
        "published_online": date_parts(record, "published-online"),
        "doi_url": doi_url,
        "acm_page_url": acm_url,
        "official_pdf_download_url": pdf_url,
        "supplementary_material_url": "",
        "source_archive_url": "",
        "github_urls": "; ".join(classes["github_urls"]),
        "repository_urls": "; ".join(classes["repository_urls"]),
        "dataset_urls": "; ".join(classes["dataset_urls"]),
        "arxiv_ids": "; ".join(classes["arxiv_ids"]),
        "arxiv_abs_urls": "; ".join(classes["arxiv_abs_urls"]),
        "arxiv_pdf_urls": "; ".join(classes["arxiv_pdf_urls"]),
        "arxiv_source_urls": "; ".join(classes["arxiv_source_urls"]),
        "all_extracted_urls": "; ".join(urls),
        "publisher": stringify(record.get("publisher", "")),
        "license_urls": "; ".join(item.get("URL", "") for item in record.get("license", []) if item.get("URL")),
        "reference_count": stringify(record.get("reference-count", "")),
        "is_referenced_by_count": stringify(record.get("is-referenced-by-count", "")),
        "crossref_type": stringify(record.get("type", "")),
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
    summary_rows = [
        ["metric", "value"],
        ["generated_at_utc", datetime.now(timezone.utc).isoformat()],
        ["source", "Crossref API"],
        ["proceedings_doi", PROCEEDINGS_DOI],
        ["rows", len(rows)],
        ["rows_with_github_urls", sum(1 for row in rows if row["github_urls"])],
        ["rows_with_repository_urls", sum(1 for row in rows if row["repository_urls"])],
        ["rows_with_dataset_urls", sum(1 for row in rows if row["dataset_urls"])],
        ["rows_with_arxiv_urls", sum(1 for row in rows if row["arxiv_ids"])],
        ["note", "Code, dataset, arXiv, and source archive links are best-effort extractions from Crossref text fields; ACM DL article bodies were not scraped."],
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
    parser = argparse.ArgumentParser(description="Scrape ICMR 2026 metadata from Crossref.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    outdir = Path(args.outdir)
    fetched_at = datetime.now(timezone.utc).isoformat()
    cache_path = outdir / "crossref_icmr2026_raw.json"
    records = fetch_crossref(cache_path, args.refresh)
    rows = [record_to_row(record, fetched_at) for record in records]
    if not rows:
        raise RuntimeError("No ICMR 2026 Crossref records found.")
    fieldnames = list(rows[0].keys())
    csv_path = outdir / "icmr2026_papers_metadata.csv"
    jsonl_path = outdir / "icmr2026_papers_metadata.jsonl"
    xlsx_path = outdir / "icmr2026_papers_metadata.xlsx"
    write_csv(csv_path, rows, fieldnames)
    write_jsonl(jsonl_path, rows)
    write_xlsx(xlsx_path, rows, fieldnames)
    print(f"rows={len(rows)}")
    print(f"csv={csv_path}")
    print(f"jsonl={jsonl_path}")
    print(f"xlsx={xlsx_path}")
    print(f"raw_cache={cache_path}")
    print("note=Code/dataset/arXiv/source links are best-effort from Crossref metadata; ACM article pages were not scraped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

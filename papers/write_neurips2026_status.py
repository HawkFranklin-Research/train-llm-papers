#!/usr/bin/env python3
"""Write a NeurIPS 2026 metadata status workbook.

As of 2026-06-16, NeurIPS 2026 accepted main-conference papers are not public.
The official call lists author notification as September 24, 2026 AoE, and the
OpenReview API has no public notes under NeurIPS.cc/2026/Conference yet.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests


DEFAULT_OUTDIR = Path("papers/neurips2026")
OPENREVIEW_GROUP_URL = "https://openreview.net/group?id=NeurIPS.cc%2F2026%2FConference"
OPENREVIEW_API_NOTES = "https://api2.openreview.net/notes"
VENUE_ID = "NeurIPS.cc/2026/Conference"
AUTHOR_NOTIFICATION = "2026-09-24 AoE"


def stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def col_name(index: int) -> str:
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx_cell(row_idx: int, col_idx: int, value: Any) -> str:
    ref = f"{col_name(col_idx)}{row_idx}"
    text = html.escape(stringify(value), quote=True)
    return f'<c r="{ref}" t="inlineStr"><is><t>{text}</t></is></c>'


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


def write_xlsx(path: Path, status_rows: list[list[Any]], paper_rows: list[list[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Status" sheetId="1" r:id="rId1"/><sheet name="Papers" sheetId="2" r:id="rId2"/></sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
        zf.writestr("xl/worksheets/sheet1.xml", xlsx_sheet(status_rows))
        zf.writestr("xl/worksheets/sheet2.xml", xlsx_sheet(paper_rows))


def main() -> int:
    parser = argparse.ArgumentParser(description="Write NeurIPS 2026 public metadata status.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR))
    args = parser.parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()

    params = {"content.venueid": VENUE_ID, "limit": 5}
    response = requests.get(OPENREVIEW_API_NOTES, params=params, timeout=30, headers={"User-Agent": "train-llm-neurips2026-status/0.1"})
    response.raise_for_status()
    notes = response.json().get("notes", [])
    raw_path = outdir / "openreview_neurips2026_probe.json"
    raw_path.write_text(json.dumps({"url": response.url, "notes": notes}, ensure_ascii=False, indent=2), encoding="utf-8")

    status = [
        {"metric": "generated_at_utc", "value": generated_at},
        {"metric": "venue_id", "value": VENUE_ID},
        {"metric": "openreview_group_url", "value": OPENREVIEW_GROUP_URL},
        {"metric": "author_notification", "value": AUTHOR_NOTIFICATION},
        {"metric": "public_notes_found_now", "value": str(len(notes))},
        {"metric": "status", "value": "Accepted NeurIPS 2026 main-conference papers are not publicly available yet."},
        {"metric": "next_action", "value": "Rerun after the September 24, 2026 AoE author notification/public posting period."},
    ]
    csv_path = outdir / "neurips2026_status.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(status)

    paper_header = [
        "openreview_id",
        "submission_number",
        "venue",
        "title",
        "authors",
        "openreview_forum_url",
        "official_pdf_download_url",
        "supplementary_material_url",
        "github_urls",
        "dataset_urls",
        "arxiv_ids",
        "arxiv_source_urls",
    ]
    xlsx_path = outdir / "neurips2026_status.xlsx"
    write_xlsx(xlsx_path, [["metric", "value"]] + [[row["metric"], row["value"]] for row in status], [paper_header])
    print(f"public_notes_found_now={len(notes)}")
    print(f"csv={csv_path}")
    print(f"xlsx={xlsx_path}")
    print(f"raw_probe={raw_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

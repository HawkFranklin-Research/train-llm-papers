#!/usr/bin/env python3
"""Download PDFs and related assets from conference metadata CSV files.

The script is resume-friendly:

- completed downloads are skipped on later runs;
- in-progress files use a `.part` suffix;
- if a `.part` file exists, HTTP Range resume is attempted;
- a status CSV is updated after each asset.

Default metadata inputs are the local ICLR 2026, ICML 2026, and NeurIPS 2025
CSV files generated in this repository.

Examples:
    # Stratified smoke test: 30 papers total across default conferences
    python papers/download_conference_assets.py --smoke --sample-size 30

    # Download everything from the default metadata files
    python papers/download_conference_assets.py --all

    # Resume a previous run
    python papers/download_conference_assets.py --all
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qs, urlparse

import requests


DEFAULT_METADATA = [
    ("iclr2026", Path("papers/iclr2026/iclr2026_accepted_papers_metadata.csv")),
    ("icml2026", Path("papers/icml2026/icml2026_papers_metadata.csv")),
    ("neurips2025", Path("papers/neurips2025/neurips2025_accepted_papers_metadata.csv")),
]
DEFAULT_OUTDIR = Path("papers/downloads")
STATUS_FIELDS = [
    "asset_id",
    "conference",
    "paper_id",
    "title",
    "asset_type",
    "url",
    "dest_path",
    "status",
    "http_status",
    "bytes",
    "sha256",
    "error",
    "updated_at_utc",
]
OPENREVIEW_FORUM_RE = re.compile(r"https?://openreview\.net/forum\?id=([A-Za-z0-9_-]+)")
URL_SPLIT_RE = re.compile(r"\s*;\s*")


@dataclass(frozen=True)
class PaperRow:
    conference: str
    row: dict[str, str]


@dataclass(frozen=True)
class Asset:
    asset_id: str
    conference: str
    paper_id: str
    title: str
    asset_type: str
    url: str
    dest_path: Path


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str, max_len: int = 120) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("._-")
    return (value or "untitled")[:max_len]


def extension_from_url(url: str, default: str) -> str:
    path = urlparse(url).path
    name = os.path.basename(path)
    if "." in name:
        ext = "." + name.rsplit(".", 1)[1].lower()
        if len(ext) <= 10:
            return ext
    return default


def split_urls(value: str) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in URL_SPLIT_RE.split(value) if part.strip().startswith(("http://", "https://"))]


def paper_id(row: dict[str, str]) -> str:
    for key in ("openreview_id", "forum_id", "icml_event_id", "doi", "paper_id", "submission_number"):
        if row.get(key):
            return slugify(row[key], 80)
    title = row.get("title", "")
    return slugify(title, 80)


def title_of(row: dict[str, str]) -> str:
    return row.get("title", "").strip() or "untitled"


def openreview_pdf_from_metadata(row: dict[str, str]) -> str:
    if row.get("official_pdf_download_url"):
        return row["official_pdf_download_url"]
    for key in ("openreview_forum_url", "all_extracted_urls"):
        for url in split_urls(row.get(key, "")) or [row.get(key, "")]:
            match = OPENREVIEW_FORUM_RE.search(url or "")
            if match:
                return f"https://openreview.net/pdf?id={match.group(1)}"
    if row.get("openreview_id"):
        return f"https://openreview.net/pdf?id={row['openreview_id']}"
    return ""


def normalize_asset_url(row: dict[str, str], asset_type: str, url: str) -> str:
    if asset_type == "supplementary" and "openreview.net/attachment/" in url:
        pid = row.get("openreview_id") or row.get("forum_id") or ""
        if not pid:
            for key in ("openreview_forum_url", "all_extracted_urls"):
                for candidate in split_urls(row.get(key, "")) or [row.get(key, "")]:
                    match = OPENREVIEW_FORUM_RE.search(candidate or "")
                    if match:
                        pid = match.group(1)
                        break
                if pid:
                    break
        if pid:
            return f"https://openreview.net/attachment?id={pid}&name=supplementary_material"
    return url


def add_asset(
    assets: list[Asset],
    seen: set[tuple[str, str, str]],
    conference: str,
    row: dict[str, str],
    asset_type: str,
    url: str,
    outdir: Path,
    default_ext: str,
) -> None:
    url = normalize_asset_url(row, asset_type, url)
    if not url or not url.startswith(("http://", "https://")):
        return
    pid = paper_id(row)
    title = title_of(row)
    key = (conference, pid, url)
    if key in seen:
        return
    seen.add(key)
    ext = extension_from_url(url, default_ext)
    filename = f"{pid}__{slugify(title, 90)}__{asset_type}{ext}"
    dest = outdir / conference / asset_type / filename
    asset_id = hashlib.sha1(f"{conference}|{pid}|{asset_type}|{url}".encode("utf-8")).hexdigest()[:16]
    assets.append(Asset(asset_id, conference, pid, title, asset_type, url, dest))


def assets_for_row(paper: PaperRow, outdir: Path, asset_types: set[str]) -> list[Asset]:
    row = paper.row
    assets: list[Asset] = []
    seen: set[tuple[str, str, str]] = set()
    conference = paper.conference

    if "paper_pdf" in asset_types:
        add_asset(assets, seen, conference, row, "paper_pdf", openreview_pdf_from_metadata(row), outdir, ".pdf")

    if "content_pdf" in asset_types:
        add_asset(assets, seen, conference, row, "content_pdf", row.get("content_pdf_url", ""), outdir, ".pdf")

    if "supplementary" in asset_types:
        add_asset(assets, seen, conference, row, "supplementary", row.get("supplementary_material_url", ""), outdir, ".bin")

    if "source_archive" in asset_types:
        add_asset(assets, seen, conference, row, "source_archive", row.get("source_archive_url", ""), outdir, ".bin")
        for url in split_urls(row.get("arxiv_source_urls", "")):
            add_asset(assets, seen, conference, row, "arxiv_source", url, outdir, ".tar")

    if "arxiv_pdf" in asset_types:
        for url in split_urls(row.get("arxiv_pdf_urls", "")):
            add_asset(assets, seen, conference, row, "arxiv_pdf", url, outdir, ".pdf")

    if "slides" in asset_types:
        for column, value in row.items():
            column_l = column.lower()
            if any(token in column_l for token in ("slide", "slides", "deck", "presentation")):
                for url in split_urls(value):
                    add_asset(assets, seen, conference, row, "slides", url, outdir, ".bin")
        for url in split_urls(row.get("all_extracted_urls", "")):
            url_l = url.lower()
            if any(token in url_l for token in ("slide", "slides", "deck", "presentation")):
                add_asset(assets, seen, conference, row, "slides", url, outdir, ".bin")

    return assets


def load_metadata(metadata_specs: list[tuple[str, Path]]) -> list[PaperRow]:
    rows: list[PaperRow] = []
    for conference, path in metadata_specs:
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                rows.append(PaperRow(conference=conference, row=row))
    return rows


def stratified_sample(rows: list[PaperRow], sample_size: int, seed: int, outdir: Path, asset_types: set[str]) -> list[PaperRow]:
    rng = random.Random(seed)
    by_conf: dict[str, list[PaperRow]] = {}
    for row in rows:
        if assets_for_row(row, outdir, asset_types):
            by_conf.setdefault(row.conference, []).append(row)
    conferences = sorted(by_conf)
    if not conferences:
        return []
    base = sample_size // len(conferences)
    remainder = sample_size % len(conferences)
    selected: list[PaperRow] = []
    for idx, conf in enumerate(conferences):
        n = base + (1 if idx < remainder else 0)
        candidates = by_conf[conf]
        selected.extend(rng.sample(candidates, min(n, len(candidates))))
    rng.shuffle(selected)
    return selected


def load_status(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as fh:
        return {row["asset_id"]: row for row in csv.DictReader(fh)}


def write_status(path: Path, rows: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows[key] for key in sorted(rows))
    tmp.replace(path)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def completed(asset: Asset, status_rows: dict[str, dict[str, str]]) -> bool:
    status = status_rows.get(asset.asset_id, {})
    if status.get("status") != "ok":
        return False
    try:
        expected = int(status.get("bytes", "0"))
    except ValueError:
        expected = 0
    return asset.dest_path.exists() and asset.dest_path.stat().st_size == expected and expected > 0


def status_row(asset: Asset, status: str, http_status: str = "", error: str = "") -> dict[str, str]:
    size = asset.dest_path.stat().st_size if asset.dest_path.exists() else 0
    digest = sha256_file(asset.dest_path) if status == "ok" and asset.dest_path.exists() else ""
    return {
        "asset_id": asset.asset_id,
        "conference": asset.conference,
        "paper_id": asset.paper_id,
        "title": asset.title,
        "asset_type": asset.asset_type,
        "url": asset.url,
        "dest_path": str(asset.dest_path),
        "status": status,
        "http_status": http_status,
        "bytes": str(size),
        "sha256": digest,
        "error": error[:500],
        "updated_at_utc": now_utc(),
    }


def download_asset(session: requests.Session, asset: Asset, timeout: int, retries: int) -> tuple[str, str, str]:
    asset.dest_path.parent.mkdir(parents=True, exist_ok=True)
    part_path = asset.dest_path.with_suffix(asset.dest_path.suffix + ".part")
    last_error = ""
    for attempt in range(1, retries + 1):
        headers = {}
        mode = "wb"
        existing = part_path.stat().st_size if part_path.exists() else 0
        if existing:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"
        try:
            with session.get(asset.url, stream=True, timeout=timeout, headers=headers, allow_redirects=True) as response:
                http_status = str(response.status_code)
                if response.status_code == 416 and part_path.exists():
                    part_path.replace(asset.dest_path)
                    return "ok", http_status, ""
                if response.status_code not in (200, 206):
                    last_error = f"HTTP {response.status_code}"
                    time.sleep(min(2 * attempt, 10))
                    continue
                if existing and response.status_code == 200:
                    mode = "wb"
                with part_path.open(mode + ("" if "b" in mode else "b")) as fh:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            fh.write(chunk)
                if part_path.stat().st_size == 0:
                    last_error = "empty response body"
                    continue
                part_path.replace(asset.dest_path)
                return "ok", http_status, ""
        except Exception as exc:
            last_error = repr(exc)
            time.sleep(min(2 * attempt, 10))
    return "error", "", last_error


def download_asset_task(asset: Asset, timeout: int, retries: int) -> tuple[Asset, str, str, str]:
    session = requests.Session()
    session.headers.update({"User-Agent": "train-llm-conference-asset-downloader/0.1"})
    status, http_status, error = download_asset(session, asset, timeout, retries)
    return asset, status, http_status, error


def parse_metadata_args(values: list[str] | None) -> list[tuple[str, Path]]:
    if not values:
        return DEFAULT_METADATA
    specs = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--metadata must be conference=path, got: {value}")
        conference, path = value.split("=", 1)
        specs.append((conference, Path(path)))
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description="Download conference PDFs and related assets from metadata CSVs.")
    parser.add_argument("--metadata", action="append", help="Metadata input as conference=path. Repeatable.")
    parser.add_argument("--outdir", default=str(DEFAULT_OUTDIR), help="Download root directory.")
    parser.add_argument("--status-file", default="", help="Status CSV path. Defaults under outdir.")
    parser.add_argument("--all", action="store_true", help="Download all rows.")
    parser.add_argument("--smoke", action="store_true", help="Run a stratified random sample instead of all rows.")
    parser.add_argument("--sample-size", type=int, default=30, help="Smoke-test paper count across conferences.")
    parser.add_argument("--seed", type=int, default=20260616, help="Random seed for smoke test.")
    parser.add_argument(
        "--asset-types",
        default="paper_pdf,supplementary,source_archive,arxiv_pdf,slides",
        help="Comma-separated: paper_pdf,content_pdf,supplementary,source_archive,arxiv_pdf,slides",
    )
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--delay", type=float, default=0.0, help="Seconds to sleep between asset downloads.")
    parser.add_argument("--jitter", type=float, default=0.0, help="Extra random delay range between downloads.")
    parser.add_argument("--workers", type=int, default=12, help="Concurrent download workers.")
    args = parser.parse_args()

    if not args.all and not args.smoke:
        raise SystemExit("Choose --smoke or --all.")

    outdir = Path(args.outdir)
    status_path = Path(args.status_file) if args.status_file else outdir / "download_status.csv"
    asset_types = {item.strip() for item in args.asset_types.split(",") if item.strip()}
    metadata_specs = parse_metadata_args(args.metadata)
    rows = load_metadata(metadata_specs)
    selected_rows = stratified_sample(rows, args.sample_size, args.seed, outdir, asset_types) if args.smoke else rows

    assets: list[Asset] = []
    seen_assets: set[str] = set()
    for row in selected_rows:
        for asset in assets_for_row(row, outdir, asset_types):
            if asset.asset_id not in seen_assets:
                assets.append(asset)
                seen_assets.add(asset.asset_id)

    manifest_path = outdir / ("smoke_manifest.csv" if args.smoke else "asset_manifest.csv")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[field for field in STATUS_FIELDS if field not in {"status", "http_status", "bytes", "sha256", "error", "updated_at_utc"}])
        writer.writeheader()
        for asset in assets:
            writer.writerow({
                "asset_id": asset.asset_id,
                "conference": asset.conference,
                "paper_id": asset.paper_id,
                "title": asset.title,
                "asset_type": asset.asset_type,
                "url": asset.url,
                "dest_path": str(asset.dest_path),
            })

    status_rows = load_status(status_path)
    ok = skipped = errors = 0
    print(f"papers_selected={len(selected_rows)}")
    print(f"assets_planned={len(assets)}")
    print(f"manifest={manifest_path}")
    print(f"status_file={status_path}")
    pending = [asset for asset in assets if not completed(asset, status_rows)]
    skipped = len(assets) - len(pending)
    for asset in assets:
        if completed(asset, status_rows):
            print(f"skip {asset.conference} {asset.asset_type} {asset.paper_id}")
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {}
        for asset in pending:
            futures[executor.submit(download_asset_task, asset, args.timeout, args.retries)] = asset
        for index, future in enumerate(as_completed(futures), start=1):
            asset, status, http_status, error = future.result()
            status_rows[asset.asset_id] = status_row(asset, status, http_status, error)
            write_status(status_path, status_rows)
            if status == "ok":
                ok += 1
            else:
                errors += 1
                print(f"error {asset.conference} {asset.asset_type} {asset.paper_id}: {error}")
            if args.delay or args.jitter:
                time.sleep(max(0.0, args.delay) + random.random() * max(0.0, args.jitter))

    print(f"ok={ok}")
    print(f"skipped={skipped}")
    print(f"errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

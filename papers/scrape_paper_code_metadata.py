#!/usr/bin/env python3
"""Scrape paper/code metadata from paper listing pages.

The old Papers with Code URLs currently redirect to Hugging Face Papers.
This script follows redirects and extracts the metadata that is still visible
on the resulting listing page: paper title, paper page, arXiv URL/PDF URL, and
GitHub repository URL when present.

Examples:
    python papers/scrape_paper_code_metadata.py
    python papers/scrape_paper_code_metadata.py \
      --url https://paperswithcode.com/methods/large-language-model-llm \
      --csv papers/llm_papers.csv \
      --jsonl papers/llm_papers.jsonl
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


DEFAULT_URL = "https://huggingface.co/papers/trending"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class Anchor:
    href: str
    text: str


class AnchorParser(HTMLParser):
    """Small HTML anchor extractor built on stdlib only."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[Anchor] = []
        self._href_stack: list[str | None] = []
        self._text_stack: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        self._href_stack.append(attr_map.get("href"))
        self._text_stack.append([])

    def handle_data(self, data: str) -> None:
        if self._text_stack:
            self._text_stack[-1].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href_stack:
            return
        href = self._href_stack.pop()
        text_parts = self._text_stack.pop()
        if not href:
            return
        text = normalize_space(" ".join(text_parts))
        self.anchors.append(Anchor(href=href, text=text))


@dataclass
class PaperRecord:
    source_url: str
    final_url: str
    paper_id: str
    title: str
    paper_url: str
    arxiv_id: str = ""
    arxiv_url: str = ""
    pdf_url: str = ""
    github_url: str = ""
    github_stars_text: str = ""
    extra_links: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, str]:
        return {
            "source_url": self.source_url,
            "final_url": self.final_url,
            "paper_id": self.paper_id,
            "title": self.title,
            "paper_url": self.paper_url,
            "arxiv_id": self.arxiv_id,
            "arxiv_url": self.arxiv_url,
            "pdf_url": self.pdf_url,
            "github_url": self.github_url,
            "github_stars_text": self.github_stars_text,
            "extra_links": ";".join(self.extra_links),
        }


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def fetch_html(url: str, timeout: int) -> tuple[str, str]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        final_url = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
        html = response.read().decode(charset, errors="replace")
    return final_url, html


def arxiv_id_from_url(url: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^/?#]+)", url, re.I)
    if not match:
        return ""
    return match.group(1).removesuffix(".pdf")


def looks_like_paper_title(anchor: Anchor) -> bool:
    if not anchor.text:
        return False
    if not re.match(r"^/papers/\d{4}\.\d+", anchor.href):
        return False
    rejected = {"upvote", "github", "arxiv page"}
    return anchor.text.strip().lower() not in rejected


def parse_records(source_url: str, final_url: str, html: str) -> list[PaperRecord]:
    parser = AnchorParser()
    parser.feed(html)

    records: list[PaperRecord] = []
    current: PaperRecord | None = None
    seen_keys: set[tuple[str, str, str]] = set()
    final_origin = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"

    def should_keep_extra_link(record: PaperRecord, url: str) -> bool:
        parsed = urlparse(url)
        if "/login" in parsed.path:
            return False
        if url == record.paper_url:
            return False
        return any(token in url.lower() for token in ("pdf", "paper", "code", "project"))

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        key = (current.paper_id, current.title, current.github_url)
        if current.title and key not in seen_keys:
            records.append(current)
            seen_keys.add(key)
        current = None

    for anchor in parser.anchors:
        href_abs = urljoin(final_origin, anchor.href)

        if looks_like_paper_title(anchor):
            flush()
            paper_id_match = re.search(r"/papers/(\d{4}\.\d+)", anchor.href)
            paper_id = paper_id_match.group(1) if paper_id_match else ""
            current = PaperRecord(
                source_url=source_url,
                final_url=final_url,
                paper_id=paper_id,
                title=anchor.text,
                paper_url=urljoin(final_origin, anchor.href),
            )
            continue

        if current is None:
            continue

        parsed = urlparse(href_abs)
        host = parsed.netloc.lower()
        if host == "github.com" or host.endswith(".github.com"):
            current.github_url = href_abs
            current.github_stars_text = normalize_space(anchor.text.replace("GitHub", ""))
            continue

        if host == "arxiv.org":
            arxiv_id = arxiv_id_from_url(href_abs)
            current.arxiv_id = arxiv_id or current.arxiv_id
            if "/abs/" in parsed.path:
                current.arxiv_url = href_abs
            if arxiv_id:
                current.pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
            elif "/pdf/" in parsed.path:
                current.pdf_url = href_abs
            continue

        if href_abs not in current.extra_links and should_keep_extra_link(current, href_abs):
            current.extra_links.append(href_abs)

    flush()
    return records


def write_csv(path: str, rows: Iterable[dict[str, str]]) -> None:
    rows = list(rows)
    fieldnames = [
        "source_url",
        "final_url",
        "paper_id",
        "title",
        "paper_url",
        "arxiv_id",
        "arxiv_url",
        "pdf_url",
        "github_url",
        "github_stars_text",
        "extra_links",
    ]
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: str, rows: Iterable[dict[str, str]]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape paper/code listing metadata.")
    parser.add_argument("--url", default=DEFAULT_URL, help="Listing URL to scrape.")
    parser.add_argument("--csv", default="papers/paper_code_metadata.csv", help="Output CSV path.")
    parser.add_argument("--jsonl", default="papers/paper_code_metadata.jsonl", help="Output JSONL path.")
    parser.add_argument("--timeout", type=int, default=30, help="Fetch timeout in seconds.")
    args = parser.parse_args()

    final_url, html = fetch_html(args.url, args.timeout)
    records = parse_records(args.url, final_url, html)
    rows = [record.to_row() for record in records]

    write_csv(args.csv, rows)
    write_jsonl(args.jsonl, rows)

    print(f"source_url={args.url}")
    print(f"final_url={final_url}")
    print(f"records={len(rows)}")
    print(f"csv={args.csv}")
    print(f"jsonl={args.jsonl}")
    if "paperswithcode." in args.url and "huggingface.co" in final_url:
        print("note=Papers with Code URL redirected to Hugging Face Papers.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

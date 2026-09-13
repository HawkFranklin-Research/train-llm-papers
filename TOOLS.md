# TOOLS

This file records the practical tool workflows available in this repo context:

- `ScraplingServer` for scraping public web pages and downloading linked data.
- `paperclip` for biomedical papers, regulatory documents, and clinical trials.
- The `/home/prime/Documents/g3/ufo` project as a reference pattern for turning scraped links into a reproducible local dataset.

## ScraplingServer

`ScraplingServer` is an MCP scraping server. It has multiple fetch tiers. Use the simplest tier that works, then escalate only when a site requires browser behavior or anti-bot handling.

### Tool Tiers

#### `get`

Use `get` for a single low- or mid-protection URL when normal HTTP access is enough.

Best for:

- Static pages.
- Direct HTML pages.
- Pages where JavaScript rendering is not required.
- Quick extraction of Markdown, HTML, or plain text.

Important options:

- `url`: target URL.
- `extraction_type`: `markdown`, `html`, or `text`.
- `css_selector`: optional selector to extract only part of the page.
- `main_content_only`: usually `true` for article/body extraction.
- `headers`, `cookies`, `params`: request customization.
- `impersonate`: browser fingerprint to impersonate, defaulting to a recent Chrome-like profile.
- `stealthy_headers`: adds realistic browser headers.
- `retries`, `retry_delay`, `timeout`: reliability controls.

Example:

```text
get({
  "url": "https://example.com",
  "extraction_type": "markdown",
  "main_content_only": true
})
```

#### `bulk_get`

Use `bulk_get` for many low- or mid-protection URLs at once.

Best for:

- Fetching many article pages.
- Checking many direct document pages.
- Extracting the same selector from many URLs.

Example:

```text
bulk_get({
  "urls": [
    "https://example.com/page-1",
    "https://example.com/page-2"
  ],
  "extraction_type": "markdown",
  "main_content_only": true
})
```

#### `fetch`

Use `fetch` when the page needs a real browser context through Playwright.

Best for:

- JavaScript-rendered pages.
- Pages where content appears after load.
- Pages that need selector waits.
- Moderate anti-bot behavior.

Important options:

- `network_idle`: wait until network activity settles.
- `wait_selector`: wait for a CSS selector.
- `wait_selector_state`: `attached`, `visible`, `hidden`, or `detached`.
- `disable_resources`: drop images/fonts/media/etc. for speed.
- `session_id`: reuse an existing browser session.

Example:

```text
fetch({
  "url": "https://example.com/search",
  "extraction_type": "markdown",
  "network_idle": true,
  "wait_selector": "main"
})
```

#### `bulk_fetch`

Use `bulk_fetch` for many browser-backed page loads.

Best for:

- Many JS-rendered URLs.
- Pages with the same extraction selector.
- Browser-backed crawling where sessions are not critical.

Example:

```text
bulk_fetch({
  "urls": [
    "https://example.com/a",
    "https://example.com/b"
  ],
  "extraction_type": "text",
  "network_idle": true
})
```

#### `stealthy_fetch`

Use `stealthy_fetch` for high-protection pages, bot-sensitive pages, or sites where ordinary HTTP/browser fetches fail.

Best for:

- Sites returning 403 to normal HTTP clients.
- Cloudflare or similar bot checks.
- Sites needing realistic browser fingerprinting.
- Pages where real Chrome helps.

Important options:

- `solve_cloudflare`: attempt to solve Cloudflare challenges.
- `real_chrome`: launch an installed Chrome browser.
- `hide_canvas`, `block_webrtc`, `allow_webgl`: fingerprint controls.
- `google_search`: sets a Google referer by default.
- `session_id`: reuse a stealth browser session.

Example:

```text
stealthy_fetch({
  "url": "https://protected.example.com/report",
  "extraction_type": "markdown",
  "network_idle": true,
  "solve_cloudflare": true,
  "real_chrome": true,
  "timeout": 120000
})
```

#### `bulk_stealthy_fetch`

Use `bulk_stealthy_fetch` when many protected pages need the stealth path.

Example:

```text
bulk_stealthy_fetch({
  "urls": [
    "https://protected.example.com/doc-1",
    "https://protected.example.com/doc-2"
  ],
  "extraction_type": "markdown",
  "network_idle": true,
  "solve_cloudflare": true
})
```

### Browser Sessions

Use sessions when cookies, login state, anti-bot clearance, or page context should persist across calls.

#### `open_session`

Creates a persistent browser session.

Session types:

- `dynamic`: normal Playwright browser session.
- `stealthy`: stealth browser session for stronger anti-bot handling.

Example:

```text
open_session({
  "session_type": "stealthy",
  "session_id": "research-session",
  "headless": true,
  "real_chrome": true,
  "solve_cloudflare": true,
  "network_idle": true,
  "max_pages": 5
})
```

Then reuse it:

```text
stealthy_fetch({
  "session_id": "research-session",
  "url": "https://protected.example.com/page",
  "extraction_type": "markdown"
})
```

#### `list_sessions`

Lists active browser sessions.

Example:

```text
list_sessions({})
```

#### `close_session`

Closes a persistent browser session.

Example:

```text
close_session({
  "session_id": "research-session"
})
```

### Screenshots

Use `screenshot` to visually verify a page. A browser session must be open first.

Example:

```text
screenshot({
  "session_id": "research-session",
  "url": "https://example.com",
  "image_type": "png",
  "full_page": true,
  "network_idle": true
})
```

### Practical Scrapling Escalation Rule

Use this order:

1. `get` for static pages and simple HTTP.
2. `bulk_get` when many simple URLs need the same treatment.
3. `fetch` when JavaScript rendering or browser waits are needed.
4. `bulk_fetch` for many browser-backed pages.
5. `stealthy_fetch` when normal requests fail or anti-bot protection is likely.
6. `open_session` plus `stealthy_fetch` when cookies, clearance, or repeated requests matter.
7. `screenshot` when visual confirmation is needed.

## UFO Project Reference Pattern

The project at `/home/prime/Documents/g3/ufo` is a useful reference for how scraped data should be organized after acquisition.

Important files:

- `/home/prime/Documents/g3/ufo/scripts/download_ufo_files.py`
- `/home/prime/Documents/g3/ufo/scripts/recover_missing_ufo_file.py`
- `/home/prime/Documents/g3/ufo/DATASET.md`
- `/home/prime/Documents/g3/ufo/ufo_release_metadata.csv`

### `download_ufo_files.py`

This is the metadata-first bulk download pattern.

It does the following:

1. Loads a metadata CSV.
2. Normalizes important fields.
3. Infers missing agency/type values where possible.
4. Sanitizes titles and filenames.
5. Preserves source filenames when the URL contains a usable basename.
6. Classifies files into groups such as `PDF`, `IMG`, and `VID`.
7. Downloads each file into an organized directory layout.
8. Writes a simplified metadata CSV with local output paths.
9. Records failures instead of silently ignoring them.

The storage pattern is:

```text
output_dir/
  Agency/
    PDF/
    IMG/
    VID/
```

This is the correct shape for a reproducible scrape: do not only save raw files; save the metadata that explains where each file came from and where it lives locally.

### `recover_missing_ufo_file.py`

This is the blocked-download recovery pattern.

The important idea is:

1. Direct HTTP downloads can fail with 403 or anti-bot rejection.
2. A real browser session can load the public page successfully.
3. Once inside that browser session, an in-page `fetch()` can request the protected file with the browser's cookies/session context.
4. The script validates the response status, content type, and file signature before writing bytes.
5. The metadata CSV is updated after recovery.

That pattern maps directly to ScraplingServer:

- Use `stealthy_fetch` or a `stealthy` session for the page.
- Use browser context when direct requests fail.
- Validate downloaded bytes.
- Update the manifest/metadata after recovery.

### Dataset Manifest Pattern

For any serious scrape, create a manifest table with fields like:

```text
title
source_url
document_url
source_filename
file_group
content_type
download_status
output_path
source_site
retrieved_at
notes
```

Recommended workflow:

```text
source discovery
  -> manifest CSV/JSONL
  -> file/content retrieval
  -> byte/type/size validation
  -> canonical local metadata
  -> extraction/indexing
  -> analysis
```

## Paperclip

Paperclip is not a general web scraper. It is a virtual filesystem and command-line/MCP interface for biomedical papers, regulatory documents, and clinical trials.

Use Paperclip when the task involves:

- PubMed Central full-text papers.
- bioRxiv, medRxiv, or arXiv preprints.
- FDA, EMA, or PMDA regulatory documents.
- Clinical trial registries.
- Citation-grounded biomedical or regulatory synthesis.

Use Scrapling instead when the target is an arbitrary website, portal, table, PDF link collection, image/video release, or non-biomedical source.

### Corpus Layout

Paperclip exposes a virtual filesystem:

```text
/papers/          PMC, bioRxiv, medRxiv, arXiv
/fda/
  us/             US FDA documents
  jp/             Japan PMDA documents
  eu/             EMA EPAR documents
/trials/
  us/             ClinicalTrials.gov
  cn/             ChiCTR
  jp/             UMIN + JRCT
  eu/             EudraCT + CTIS + ISRCTN
  intl/           Combined international trial registries
/.gxl/            writable scratch space
```

Most documents expose:

```text
meta.json
content.lines
sections/
figures/
supplements/
```

Common ID prefixes:

- `PMC...`: PubMed Central.
- `bio_...`: bioRxiv.
- `med_...`: medRxiv.
- `arx_...`: arXiv.
- `fda_...`: regulatory document.
- `tri_...`: trial record.

### Required Search Source

Paperclip search requires a source flag.

Examples:

```bash
paperclip search -s pmc "CRISPR delivery"
paperclip search -s biorxiv "protein design"
paperclip search -s medrxiv "long COVID"
paperclip search -s arxiv "diffusion models biology"
paperclip search -s abstracts "drug discovery"
paperclip search -s fda "pembrolizumab"
paperclip search -s trials "breast cancer HER2"
paperclip search -s trials/us "NCT03928938"
```

Key options:

- `-n`, `--limit`: number of results.
- `--since`: date filter.
- `--sort relevance|date`: sort mode.
- `--author`: author filter.
- `--journal`: journal filter.
- `--year`: publication year.
- `--ranking hybrid|bm25|vector`: ranking strategy.

Example:

```bash
paperclip search -s pmc "GLP-1 receptor agonists" -n 5
```

The result is saved under an ID like `s_56c3f9a4`, which can be reused by other commands.

### Reading Documents

Use virtual filesystem commands:

```bash
paperclip ls /papers/PMC12560356/
paperclip cat /papers/PMC12560356/meta.json
paperclip head -50 /papers/PMC12560356/content.lines
paperclip grep "primary endpoint" /papers/PMC12560356/content.lines
paperclip scan /papers/PMC12560356/content.lines "dose" "adverse event" "endpoint"
```

Use `content.lines` for citations because it includes line numbers.

Prefer:

- `head`
- `grep`
- `scan`
- specific `sections/` files

Avoid dumping huge full-text files unless necessary.

### Map And Reduce

Use `map` to ask the same question across search results.

```bash
paperclip search -s pmc "GLP-1 receptor agonists fatty liver randomized trial" -n 5
paperclip map --from s_xxx "Extract disease, intervention, comparator, sample size, primary endpoint, and main result."
```

Use `reduce` to synthesize mapped results.

```bash
paperclip reduce --from m_xxx --strategy table "Compare interventions, endpoints, and outcomes."
```

Useful reduce strategies:

- `summarize`
- `table`
- `themes`
- `consensus`
- `bullet_points`
- `extract`

Keep map/reduce sets small, usually 3 to 10 papers.

### Repositories And Claim Verification

Use Paperclip repos when claims need verification before citation.

Recommended workflow:

```bash
paperclip repo init my-review
paperclip search -s pmc "topic" -n 10
paperclip map --from s_xxx "What was the main finding and sample size?"
paperclip repo add PMC123456 "The paper reports X as the primary finding." --lines L45-L52
paperclip repo commit -m "Initial verified claims"
paperclip repo status
```

Only cite claims marked `[OK]` in `repo status`.

If a claim is marked `[X]`:

1. Revise the claim.
2. Find better supporting lines.
3. Replace the paper/source.
4. Or drop the claim.

Always run:

```bash
paperclip repo status
```

before writing a final citation-heavy answer.

### Importing Papers

Paperclip can import local PDFs, bibliography files, or references from a paper.

Examples:

```bash
paperclip import paper.pdf
paperclip import ~/papers/
paperclip import refs.bib
paperclip import refs.ris
paperclip import PMC11282385 --min-cites 50
paperclip import ~/papers/ --dry-run
paperclip import refs.bib --init my-review
paperclip import ~/papers/ --add-to-repo
```

Imported papers go into the personal Paperclip library first. Use `--add-to-repo` to also add them to the active repo, or `--init` to create a new repo.

### Library Commands

Examples:

```bash
paperclip library
paperclip library --matched
paperclip library --unmatched
paperclip library -s "fine-tuning"
paperclip library PMC11166971
paperclip library --rematch
```

### Results Commands

Examples:

```bash
paperclip results --list
paperclip results s_56c3f9a4
paperclip results s_56c3f9a4 --save results.csv
```

### SQL

Use SQL for corpus-level counts or metadata queries.

Example:

```bash
paperclip sql "SELECT pub_year, COUNT(*) FROM documents WHERE title ILIKE '%CRISPR%' GROUP BY pub_year ORDER BY pub_year"
```

The SQL interface allows `SELECT` on the `documents` table.

Common columns:

```text
id
title
doi
authors
source
abstract_text
pub_date
journal_title
article_type
pmid
keywords
categories
pub_year
```

### Citation Format

Use line-numbered citations from `content.lines`.

Inline:

```text
The study reported the endpoint result [1].
```

References:

```text
--------
REFERENCES
[1] Authors. "Title." Journal vol, pages (year). doi:XX
    https://citations.gxl.ai/papers/<doc_id>#L45-L52
```

For regulatory and trial documents, use:

```text
https://citations.gxl.ai/fda/<doc_id>#L45
https://citations.gxl.ai/trials/<doc_id>#L45
```

Do not expose internal document IDs in prose except inside citation URLs.

## Choosing Between Scrapling And Paperclip

Use ScraplingServer when:

- The source is a public website.
- The source is a portal, table, release page, or document index.
- You need to discover and download arbitrary PDFs, images, videos, CSVs, or HTML pages.
- The site requires browser rendering, cookies, screenshots, or anti-bot handling.

Use Paperclip when:

- The source should be biomedical literature.
- The source should be FDA/EMA/PMDA documents.
- The source should be clinical trial registry records.
- You need line-numbered citations and claim verification.
- You need map/reduce extraction over papers.

Use both when:

- A public website lists relevant biomedical/regulatory documents that should be mirrored locally.
- Scrapling can discover/download a manifest, while Paperclip can cross-check papers, trials, and regulatory documents.

Recommended combined workflow:

```text
1. Use ScraplingServer to discover source pages and document links.
2. Write a manifest CSV/JSONL.
3. Download or recover files using get/fetch/stealthy_fetch as needed.
4. Validate local files and update metadata.
5. Use Paperclip for biomedical literature, regulatory, or trial cross-references.
6. Use Paperclip repos for any citation-sensitive claims.
7. Produce analysis only after source paths and citations are reproducible.
```


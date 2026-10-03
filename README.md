# riordino

[![CI](https://github.com/ale-grassi/riordino/actions/workflows/ci.yml/badge.svg)](https://github.com/ale-grassi/riordino/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/riordino)](https://pypi.org/project/riordino/)
[![Python](https://img.shields.io/pypi/pyversions/riordino)](https://pypi.org/project/riordino/)
[![License](https://img.shields.io/github/license/ale-grassi/riordino)](https://github.com/ale-grassi/riordino/blob/main/LICENSE)
[![GHCR](https://img.shields.io/badge/ghcr.io-riordino-blue?logo=docker)](https://ghcr.io/ale-grassi/riordino)

**Intelligent scanned PDF organizer** — Splits a bulk-scanned PDF into separate, well-named documents using AI.

`riordino` takes one or more PDFs (the kind you get from scanning an entire stack of mixed paperwork at once) and treats them as a single bulk to analyze.

## How It Works

```
┌────────────────┐
│  Input PDF(s)  │  (bulk scans with mixed documents)
└──────┬─────────┘
       ▼
 1. Load & merge input PDFs, render pages
       ▼
 2. Detect and remove blank pages
       ▼
 3. Detect and correct rotation (Tesseract OSD)
       ▼
 4. Analyze each page with Gemini (batched)
    → title, date, type, subject, priority, description
       ▼
 5. Aggregate pages into logical document groups
       ▼
 6. Determine correct page order within each group
       ▼
 7. Split PDF and write output files
       ▼
┌──────────────────────────────────────────────┐
│  Output: separate PDFs + JSON metadata each  │
└──────────────────────────────────────────────┘
```

## Features

- **Blank page removal** — detects and strips scanner-introduced blank pages using pixel variance analysis
- **Automatic rotation correction** — uses Tesseract OSD to detect and fix page orientation
- **AI-powered analysis** — Google Gemini extracts structured metadata from each page (title, date, type, priority, and more)
- **Smart document grouping** — pages are aggregated into logical documents based on content, subject, dates, and page numbering
- **Intelligent page ordering** — reconstructs the correct reading order within each document
- **Descriptive filenames** — output files are named with dates, subjects, and descriptions (e.g., `2024-03_ccss_certificate.pdf`)
- **Selective API retries** — transient Gemini failures are retried with exponential backoff; invalid structured responses fail fast
- **Configurable language support** — `--language` flag constrains Tesseract and Gemini to specific languages (23 languages supported)
- **Selective pipeline control** — skip individual steps with `--skip-blanks`, `--skip-rotation`, `--skip-analysis`, `--skip-aggregation`, `--skip-ordering`
- **Dependency checks** — verifies Tesseract, language packs, and API keys before running
- **Dry-run mode** — preview the plan without writing any files
- **Step-by-step debugging** — optionally save all intermediate outputs for inspection


## OpenAI-compatible providers and LiteLLM (this fork)

All three AI stages (page analysis, grouping and ordering) can use an OpenAI-compatible
Chat Completions endpoint. LiteLLM can route these requests to its configured providers;
Riordino needs only the gateway URL, a gateway key and a model alias. Direct Gemini
remains the default. An API key alone does not make every model compatible: page
analysis and ordering require **image input**, and every stage requires valid JSON.

Example `.env` for a gateway (replace placeholders; never commit real keys):

```dotenv
RIORDINO_PROVIDER=openai
RIORDINO_BASE_URL=http://localhost:4000/v1
RIORDINO_MODEL=your-vision-model-alias
RIORDINO_API_KEY_ENV=RIORDINO_GATEWAY_KEY
RIORDINO_GATEWAY_KEY=replace-with-your-gateway-key
RIORDINO_RESPONSE_FORMAT=json_schema
```

Use the reachable gateway hostname in place of `localhost` for a remote gateway.
The model name is the **gateway alias**, not necessarily the upstream provider's model ID.
Provider keys stay on LiteLLM; use a limited application key for Riordino.
For direct OpenAI, omit `RIORDINO_BASE_URL` and use `OPENAI_API_KEY` (omit the custom
`RIORDINO_API_KEY_ENV` setting). Other OpenAI-compatible servers use their own base URL.
Native provider protocols such as Anthropic's should be accessed through LiteLLM.

```sh
# Analyse without writing output PDFs, preserving blanks and original page order:
riordino batch.pdf --dry-run --skip-blanks --skip-ordering

# Write to a staging folder for review, NOT directly to Paperless's consume folder:
riordino batch.pdf -o output/review --skip-blanks --skip-ordering --save-steps
```

`--dry-run` still sends images to the configured model endpoint. Running on a Mac
only keeps inference local if the selected gateway route uses a local model;
cloud routes (including gateway fallbacks) send content to that provider.
No automatic switch to another provider or response mode happens in Riordino.

Configuration flags override environment variables:

| Flag | Environment | Default |
| --- | --- | --- |
| `--provider gemini\|openai` | `RIORDINO_PROVIDER` | `gemini` |
| `--base-url` | `RIORDINO_BASE_URL` | OpenAI public API for `openai` |
| `--model` | `RIORDINO_MODEL` | Required for `openai`; existing Gemini default otherwise |
| `--api-key-env` | `RIORDINO_API_KEY_ENV` | `OPENAI_API_KEY` or `GOOGLE_API_KEY` |
| `--response-format json_schema\|json_object` | `RIORDINO_RESPONSE_FORMAT` | `json_schema` |
| `--request-timeout` | — | 120 seconds |

`json_schema` requests strict structured output. For models without that capability,
explicitly select `json_object`; Riordino still validates the JSON against the same
schema. Missing/duplicate/out-of-range page groups, incomplete responses and refusals
fail before output PDFs are written. Transient connection/rate-limit/server failures
use `--max-retries`; authentication and schema failures are not retried. Credentials
are read from environment variables, never command-line key arguments.

This addition provides model transport, not a review GUI, inbox watcher or automatic
Paperless import. Split quality still needs evaluation on representative scans.

## Prerequisites

- **Python 3.14+**
- **Tesseract OCR** with OSD data
- **Google API key** with access to the Gemini API

### Installing Tesseract

```bash
# Arch Linux
sudo pacman -S tesseract tesseract-data-osd

# Ubuntu / Debian
sudo apt install tesseract-ocr tesseract-ocr-osd

# macOS
brew install tesseract
```

Install language data packages for each language you plan to use (riordino checks for these at startup):

```bash
# Arch Linux (example: German, French, Italian, Polish)
sudo pacman -S tesseract-data-deu tesseract-data-fra tesseract-data-ita tesseract-data-pol

# Ubuntu / Debian
sudo apt install tesseract-ocr-deu tesseract-ocr-fra tesseract-ocr-ita tesseract-ocr-pol
```

## Installation

```bash
git clone https://github.com/tmault/riordino.git
cd riordino

python -m venv .venv
source .venv/bin/activate

pip install .
```

Create a `.env` file in the project root:

```bash
GOOGLE_API_KEY=your_api_key_here
RIORDINO_LANGUAGES=en,de,fr,it,pl
```

`RIORDINO_LANGUAGES` sets the default for `--language`. If omitted, defaults to `en`.

### Docker

```bash
docker build -t riordino .

# Install additional language packs at build time:
docker build -t riordino --build-arg LANGS="deu fra ita" .
```

```bash
docker run --rm \
  -e GOOGLE_API_KEY \
  -v "$PWD":/data \
  riordino /data/scan.pdf -o /data/output/
```

### Development setup

```bash
pip install -e '.[dev]'  # installs ruff, mypy, pytest
```

## Usage

```bash
# Single PDF
python riordino.py scan.pdf

# Multiple PDFs (merged into one bulk for analysis)
python riordino.py scan1.pdf scan2.pdf scan3.pdf
```

This processes the input PDF(s) and writes the split documents to the same directory as the first input file.

## CLI Reference

| Option | Default | Description |
|---|---|---|
| `input_pdf` | *(required)* | Path(s) to scanned PDF file(s) — multiple files are merged into one bulk |
| `-o`, `--output-dir` | Same as input file | Directory for output files |
| `-b`, `--blank-threshold` | `0.001` | Pixel variance threshold for blank page detection (0.0–1.0) |
| `-n`, `--dry-run` | off | Show the processing plan without writing any files |
| `--dpi` | `150` | DPI for page rendering (72–600) |
| `--model` | `gemini-3.1-flash-lite-preview` | Gemini model to use |
| `-l`, `--language` | `$RIORDINO_LANGUAGES` or `en` | Comma-separated ISO 639-1 language codes |
| `--batch-size` | `10` | Number of pages per LLM analysis batch (1–50) |
| `--max-retries` | `3` | Maximum API retry attempts on failure (0–10) |
| `--save-steps` | off | Save intermediate outputs to an `_steps/` subdirectory |
| `--skip-blanks` | off | Skip blank page detection (keep all pages) |
| `--skip-rotation` | off | Skip rotation detection and correction |
| `--skip-analysis` | off | Skip LLM page analysis (implies `--skip-aggregation`) |
| `--skip-aggregation` | off | Skip LLM document grouping (implies `--skip-ordering`) |
| `--skip-ordering` | off | Skip LLM page ordering within documents |

## Next Steps

- [ ] Add a `--verbose` / `--quiet` flag for log level control
- [ ] Explore local/open-source LLM backends as an alternative to Gemini
- [ ] Support OCR-based text extraction as a fallback when Gemini is unavailable

### Mac bulk-scan operator workflow

`scripts/mac_bulk.py` adds a manual workflow for an existing local Gemma vision
server on `127.0.0.1:8003`. It fetches PDFs over SSH from a separate Tower bulk
inbox, preserves originals by checksum and writes split PDFs into `Review`.
It uses concise analysis, one page per request, one model request at a time and
a 300-second timeout. Blank removal, rotation and reordering are disabled to
preserve the scanned pages; feed pages upright. The existing local model key is
read from `~/Library/Application Support/gemma4-e4b/api.key`, without copying it.

```sh
python scripts/mac_bulk.py process --workspace "$HOME/Documents/Codex/Bulk Scans"
```

Review each batch's PDFs and `Review.html`, then copy only approved PDFs into
`Approved`. Explicit import asks you to type `IMPORT` and submits them atomically
to Paperless's consume folder:

```sh
python scripts/mac_bulk.py import --workspace "$HOME/Documents/Codex/Bulk Scans"
```

`--host`, `--remote-inbox`, `--workspace` and `--key-file` are configurable.
Defaults target `root@tower.local`, `/mnt/user/data/paperless/bulk-inbox` and the
existing local model. This operator script assumes your pre-existing SSH access;
it does not provision credentials. The remote import helper targets
`/mnt/user/data/paperless/consume` and records checksums in sibling `bulk-imported`.
An interrupted import is held as `pending` for reconciliation instead of retried
blindly. Input, review and approved copies are retained. This is a file-based
review workflow, not a graphical boundary editor. Check the imported document's
status in Paperless; file delivery alone does not prove successful consumption.

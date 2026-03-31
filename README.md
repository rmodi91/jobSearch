# JobSearch

Automated job discovery and CV tailoring. Scrapes job listings from LinkedIn, TrueUp, and company career pages, logs them in a Google Sheet, and uses Gemini AI to tailor your CV for each new listing.

> Does not auto-apply. It finds jobs and prepares your CV — you apply manually.

---

## What it does

1. **Scrapes** job listings from LinkedIn (via `python-jobspy`), TrueUp (via Playwright + CDP), and any company career pages you configure
2. **Deduplicates** against your Google Sheet — only new jobs are processed
3. **Writes** each new job to the sheet (title, company, location, description, apply URL, source, date)
4. **Scores** each job (1–10) against your master CV using Gemini AI
5. **Creates** a tailored Google Doc CV for each job scoring above your threshold
6. **Links** the tailored CV back in the sheet row
7. Runs **on demand** or on a **daily schedule**

---

## Setup

### 1. Python environment

```bash
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → create a project
2. Enable: **Sheets API**, **Docs API**, **Drive API**
3. **IAM & Admin → Service Accounts → Create Service Account** → download the JSON key
4. Save as `credentials/service_account.json`
5. Note the service account email (e.g. `jobsearch-bot@your-project.iam.gserviceaccount.com`)

### 3. Google Drive setup

| Resource | Action |
|---|---|
| **Google Sheet** "JobSearch Results" | Create → Share with service account as **Editor** → copy Sheet ID from URL |
| **Master CV** (`.docx` uploaded to Drive) | Share with service account as **Viewer** → copy File ID from URL |
| **Folder** "Tailored CVs" | Create → Share with service account as **Editor** → copy Folder ID from URL |

> **How to find IDs from URLs:**
> - Sheet: `docs.google.com/spreadsheets/d/**SHEET_ID**/edit`
> - File: `drive.google.com/file/d/**FILE_ID**/view`
> - Folder: `drive.google.com/drive/folders/**FOLDER_ID**`

### 4. Environment variables

Copy and fill in `.env`:

```
GEMINI_API_KEY=your_key_from_aistudio.google.com
GOOGLE_SERVICE_ACCOUNT_FILE=credentials/service_account.json
MASTER_CV_DOC_ID=<your CV file id>
JOBS_SHEET_ID=<your sheet id>
TAILORED_CVS_FOLDER_ID=<your folder id>
```

> Get your Gemini API key from [aistudio.google.com](https://aistudio.google.com) — not from the Google Cloud Console.

### 5. Configure your search

Edit `config.yaml`:

```yaml
search:
  titles:
    - "Senior Product Manager"
    - "Director of Product"
  keywords:
    - "B2B SaaS"
    - "platform"
  locations:
    - "Germany"
    - "Berlin"
  hours_old: 400

cv_tailoring:
  model: "gemini-2.0-flash"
  min_match_score: 5   # only create tailored CV for jobs scoring >= this (1–10)
```

### 6. Verify everything works

```bash
python main.py test-auth
```

---

## Usage

### Full pipeline (LinkedIn + TrueUp + company sites)

```bash
python main.py run
```

### TrueUp — manual two-step workflow

TrueUp is behind Cloudflare and requires login. Use CDP to connect to a real Chrome window:

**Step 1** — open Chrome with remote debugging:
```bash
python main.py open-browser
```
Log in to TrueUp, navigate to the job listings page, apply your filters (location, role type, etc.).

**Step 2** — scrape from the open browser:
```bash
python main.py scrape-trueup
```
The scraper connects to the existing Chrome session, clicks "Show More" to load all listings, then extracts and processes all jobs.

### Other options

```bash
python main.py run --dry-run                  # Scrape only — no sheet writes or CV tailoring
python main.py run --no-tailor                # Write to sheet but skip CV tailoring
python main.py run --sources linkedin         # Limit to one source
python main.py run --limit 10                 # Process at most 10 new jobs
python main.py run --debug                    # Open headed browser for Playwright inspection
python main.py schedule                       # Start daily scheduler
```

---

## Google Sheet columns

| Column | Content |
|--------|---------|
| A: Hash | Internal dedup key (you can hide this column) |
| B: Date Found | YYYY-MM-DD |
| C: Job Title | |
| D: Company | |
| E: Location | |
| F: Description | Truncated to 500 chars |
| G: Apply URL | |
| H: Match Score | 1–10 from Gemini; 0 until tailoring runs |
| I: Tailored CV Link | Google Docs URL |
| J: Status | New / Reviewing / Applying / Applied / Rejected / Offer |
| K: Notes | Free text |
| L: Source | linkedin / trueup / company:stripe |

**Tip:** Add conditional formatting to column H — green ≥ 8, yellow ≥ 6, red < 6.

---

## Adding company career pages

In `config.yaml`, add entries under `company_sites.sites`:

```yaml
company_sites:
  enabled: true
  sites:
    - name: "Stripe"
      url: "https://stripe.com/jobs/search"
      scrape_method: "playwright"         # playwright for React/dynamic sites
      job_link_selector: "a.job-link"     # CSS selector for job links
      title_selector: ".job-title"
      location_selector: ".job-location"
      next_page_selector: null            # null = single page

    - name: "Twilio"
      url: "https://www.twilio.com/en-us/company/jobs"
      scrape_method: "requests"           # faster; use for static HTML
      job_link_selector: "a.posting-title"
      title_selector: null
      location_selector: ".posting-categories"
```

To find the right selectors: open the career page in Chrome DevTools → Inspect elements.

---

## Troubleshooting

**Gemini returns 429 quota errors**
- The free tier has a daily request limit. Wait until midnight Pacific for the quota to reset.
- Or enable billing on your Google AI project for higher limits.

**LinkedIn returns 0 results**
- LinkedIn rate-limits scrapers. Reduce `results_per_site` and `hours_old` in `config.yaml`.
- Wait between runs. For cloud/CI use, add residential proxies.

**TrueUp only shows a few jobs**
- Make sure you applied the right filters in the browser (location, date posted, role type) before running `scrape-trueup`.
- The scraper will click "Show More" up to `max_pages` times (default: 5 in config).

**Google CV reads as blank or garbled**
- Your `.docx` is read via `python-docx`. Complex layouts (multi-column, text boxes) may not parse well. Simplify the document structure if needed.
- Test with: `python -c "from dotenv import load_dotenv; load_dotenv(); import yaml; from src.services.google_docs import GoogleDocsService; c=yaml.safe_load(open('config.yaml')); print(GoogleDocsService(c).read_master_cv()[:500])"`

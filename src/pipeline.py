from __future__ import annotations

import logging
import os
from typing import Sequence

import yaml

from src.scrapers.company_sites import CompanySitesScraper
from src.scrapers.jobspy_scraper import JobSpyScraper
from src.scrapers.trueup import TrueUpScraper
from src.services.cv_tailor import CVTailor
from src.services.google_docs import GoogleDocsService
from src.services.google_sheets import GoogleSheetsService
from src.utils.dedup import JobDeduplicator, JobListing


def load_config(config_path: str) -> dict:
    with open(config_path) as fh:
        return yaml.safe_load(fh)


def run_pipeline(
    config_path: str = "config.yaml",
    sources: Sequence[str] | None = None,
    dry_run: bool = False,
    skip_tailoring: bool = False,
    limit: int | None = None,
    debug: bool = False,
) -> None:
    """
    Main entry point for a single job-search run.

    Args:
        config_path:     Path to config.yaml
        sources:         If provided, only run these scrapers (linkedin|trueup|company_sites)
        dry_run:         Scrape and print jobs; skip sheet writes and CV tailoring
        skip_tailoring:  Write jobs to sheet but skip CV tailoring
        limit:           Process at most N new jobs per run
        debug:           Pass headless=False to Playwright scrapers for inspection
    """
    _setup_logging()
    config = load_config(config_path)
    if debug:
        config["_debug"] = True

    logging.info("=== JobSearch pipeline starting ===")

    # Initialise services
    sheets = GoogleSheetsService(config)
    docs = GoogleDocsService(config)
    deduper = JobDeduplicator(sheets)

    # ── Scraping ────────────────────────────────────────────────────────────
    all_jobs: list[JobListing] = []

    if _source_enabled("linkedin", sources, config):
        jobs = JobSpyScraper(config).scrape()
        logging.info(f"[LinkedIn] {len(jobs)} jobs scraped")
        all_jobs.extend(jobs)

    if _source_enabled("trueup", sources, config):
        jobs = TrueUpScraper(config).scrape()
        logging.info(f"[TrueUp] {len(jobs)} jobs scraped")
        all_jobs.extend(jobs)

    if _source_enabled("company_sites", sources, config):
        jobs = CompanySitesScraper(config).scrape()
        logging.info(f"[Company sites] {len(jobs)} jobs scraped")
        all_jobs.extend(jobs)

    logging.info(f"Total scraped: {len(all_jobs)}")

    # ── Deduplication ───────────────────────────────────────────────────────
    new_jobs = deduper.filter_new(all_jobs)
    logging.info(f"New jobs (not in sheet): {len(new_jobs)}")

    if limit is not None:
        new_jobs = new_jobs[:limit]
        logging.info(f"Limiting to first {limit} new jobs")

    if not new_jobs:
        logging.info("No new jobs found — pipeline complete")
        return

    if dry_run:
        _print_dry_run(new_jobs)
        return

    # ── Write to sheet + tailor CVs ─────────────────────────────────────────
    min_score_for_tailoring = config.get("cv_tailoring", {}).get("min_match_score", 5)
    tailoring_enabled = config.get("cv_tailoring", {}).get("enabled", True) and not skip_tailoring

    master_cv: str | None = None
    if tailoring_enabled:
        try:
            master_cv = docs.read_master_cv()
        except Exception as exc:
            logging.error(f"Failed to read master CV: {exc}. Tailoring will be skipped.")
            master_cv = None

    tailor = CVTailor(config) if (tailoring_enabled and master_cv) else None

    for i, job in enumerate(new_jobs, 1):
        logging.info(f"Processing job {i}/{len(new_jobs)}: {job.company} — {job.title}")

        # Always write the row immediately (score=0, no CV link yet)
        sheets.append_job(job)

        if tailor and master_cv:
            tailored_text, score = tailor.tailor(master_cv, job)

            if score < min_score_for_tailoring:
                logging.info(
                    f"Match score {score} below threshold {min_score_for_tailoring} — "
                    f"skipping CV creation for {job.company}/{job.title}"
                )
                # Still update the score column
                sheets.update_tailored_cv(job.job_hash, "", score)
                continue

            try:
                doc_url = docs.create_tailored_cv(tailored_text, job)
                sheets.update_tailored_cv(job.job_hash, doc_url, score)
            except Exception as exc:
                logging.error(f"Failed to create tailored CV doc: {exc}")
                sheets.update_tailored_cv(job.job_hash, "", score)

    logging.info(f"=== Pipeline complete: {len(new_jobs)} new jobs processed ===")


def _source_enabled(
    name: str,
    sources: Sequence[str] | None,
    config: dict,
) -> bool:
    """Return True if this scraper should run given CLI flags and config."""
    if sources and name not in sources:
        return False
    return config.get(name, {}).get("enabled", True)


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _print_dry_run(jobs: list[JobListing]) -> None:
    print(f"\n{'='*70}")
    print(f"DRY RUN — {len(jobs)} new jobs found (no writes performed)")
    print(f"{'='*70}")
    for j in jobs:
        print(f"\n  Company:  {j.company}")
        print(f"  Title:    {j.title}")
        print(f"  Location: {j.location}")
        print(f"  Source:   {j.source}")
        print(f"  URL:      {j.url}")
        if j.description:
            print(f"  Desc:     {j.description[:120]}...")
    print()

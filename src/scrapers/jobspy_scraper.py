from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd

from src.utils.dedup import JobListing


class JobSpyScraper:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.search_cfg = config["search"]
        self.linkedin_cfg = config.get("linkedin", {})
        self.rate_cfg = config.get("rate_limiting", {})

    def scrape(self) -> list[JobListing]:
        if not self.linkedin_cfg.get("enabled", True):
            logging.info("LinkedIn scraper disabled in config")
            return []

        try:
            from jobspy import scrape_jobs
        except ImportError:
            logging.error("python-jobspy not installed. Run: pip install python-jobspy")
            return []

        results: list[JobListing] = []
        fetch_desc = self.linkedin_cfg.get("fetch_description", True)
        delay = self.rate_cfg.get("delay_between_requests", 2.5)

        for title in self.search_cfg["titles"]:
            for location in self.search_cfg["locations"]:
                logging.info(f"[LinkedIn] Searching '{title}' in '{location}'")
                try:
                    df = scrape_jobs(
                        site_name=["linkedin"],
                        search_term=title,
                        location=location,
                        results_wanted=self.search_cfg.get("results_per_site", 25),
                        hours_old=self.search_cfg.get("hours_old", 48),
                        job_type=self.search_cfg.get("job_type", "fulltime"),
                        is_remote=self.search_cfg.get("is_remote", False),
                        linkedin_fetch_description=fetch_desc,
                        description_format="markdown",
                        verbose=0,
                    )
                    batch = self._df_to_jobs(df)
                    logging.info(f"[LinkedIn] Got {len(batch)} jobs for '{title}' / '{location}'")
                    results.extend(batch)
                except Exception as exc:
                    logging.error(f"[LinkedIn] Scrape failed for '{title}' / '{location}': {exc}")

                time.sleep(delay)

        return results

    def _df_to_jobs(self, df: pd.DataFrame) -> list[JobListing]:
        jobs: list[JobListing] = []
        for _, row in df.iterrows():
            if pd.isna(row.get("title")) or pd.isna(row.get("company")):
                continue
            description = str(row.get("description") or "")
            jobs.append(
                JobListing(
                    title=str(row["title"]).strip(),
                    company=str(row["company"]).strip(),
                    location=str(row.get("location") or ""),
                    description=description[:2000],
                    url=str(row.get("job_url") or ""),
                    date_found=date.today().isoformat(),
                    source="linkedin",
                )
            )
        return jobs

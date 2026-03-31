from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse, urlunparse


@dataclass
class JobListing:
    title: str
    company: str
    location: str
    description: str
    url: str
    date_found: str
    source: str
    match_score: int = 0
    tailored_cv_link: str = ""
    status: str = "New"
    notes: str = ""
    job_hash: str = field(default="", init=False)

    def __post_init__(self) -> None:
        self.job_hash = make_job_hash(self)


def make_job_hash(job: JobListing) -> str:
    """
    Stable 16-char SHA256 hash from (company, title, normalized_url).
    URL is lowercased and query params/fragments stripped so the same job
    discovered from slightly different URLs is treated as one entry.
    """
    parsed = urlparse(job.url.lower())
    clean_url = urlunparse(parsed._replace(query="", fragment=""))
    raw = f"{job.company.lower().strip()}|{job.title.lower().strip()}|{clean_url}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class JobDeduplicator:
    def __init__(self, sheets_service) -> None:
        self._seen: set[str] = sheets_service.get_all_job_hashes()
        logging.info(f"Deduplicator loaded {len(self._seen)} existing job hashes")

    def filter_new(self, jobs: list[JobListing]) -> list[JobListing]:
        result: list[JobListing] = []
        for job in jobs:
            if job.job_hash not in self._seen:
                self._seen.add(job.job_hash)
                result.append(job)
        logging.info(f"Deduplication: {len(result)} new out of {len(jobs)} scraped")
        return result

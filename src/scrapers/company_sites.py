from __future__ import annotations

import logging
import time
from datetime import date
from urllib.parse import urljoin

from src.utils.dedup import JobListing


class CompanySitesScraper:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.cfg = config.get("company_sites", {})
        self.sites: list[dict] = self.cfg.get("sites", [])
        self.search_titles = [t.lower() for t in config["search"]["titles"]]
        self.search_keywords = [k.lower() for k in config["search"].get("keywords", [])]
        self.rate_cfg = config.get("rate_limiting", {})

    def scrape(self) -> list[JobListing]:
        if not self.cfg.get("enabled", True) or not self.sites:
            logging.info("Company sites scraper disabled or no sites configured")
            return []

        playwright_sites = [s for s in self.sites if s.get("scrape_method") == "playwright"]
        static_sites = [s for s in self.sites if s.get("scrape_method") != "playwright"]

        all_jobs: list[JobListing] = []

        if playwright_sites:
            all_jobs.extend(self._scrape_with_playwright(playwright_sites))

        for site in static_sites:
            try:
                jobs = self._scrape_static(site)
                logging.info(f"[{site['name']}] Found {len(jobs)} matching jobs")
                all_jobs.extend(jobs)
            except Exception as exc:
                logging.error(f"[{site['name']}] Static scrape failed: {exc}")
            time.sleep(self.rate_cfg.get("delay_between_sites", 5.0))

        return all_jobs

    def _scrape_with_playwright(self, sites: list[dict]) -> list[JobListing]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logging.error("playwright not installed.")
            return []

        debug = self.config.get("_debug", False)
        slow_mo = self.rate_cfg.get("playwright_slow_mo", 500)
        delay = self.rate_cfg.get("delay_between_sites", 5.0)
        all_jobs: list[JobListing] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not debug, slow_mo=slow_mo)
            page = browser.new_page()

            for site in sites:
                try:
                    jobs = self._scrape_playwright_site(page, site)
                    logging.info(f"[{site['name']}] Found {len(jobs)} matching jobs")
                    all_jobs.extend(jobs)
                except Exception as exc:
                    logging.error(f"[{site['name']}] Playwright scrape failed: {exc}")
                time.sleep(delay)

            browser.close()

        return all_jobs

    def _scrape_playwright_site(self, page, site: dict) -> list[JobListing]:
        page.goto(site["url"], wait_until="networkidle", timeout=30000)
        jobs: list[JobListing] = []

        links = page.query_selector_all(site["job_link_selector"])
        for link in links:
            try:
                title_sel = site.get("title_selector")
                loc_sel = site.get("location_selector")

                title_el = link.query_selector(title_sel) if title_sel else link
                loc_el = link.query_selector(loc_sel) if loc_sel else None

                title = (title_el.inner_text().strip() if title_el else link.inner_text().strip())
                location = loc_el.inner_text().strip() if loc_el else ""
                href = link.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = urljoin(site["url"], href)

                if self._matches_search(title):
                    jobs.append(
                        JobListing(
                            title=title,
                            company=site["name"],
                            location=location,
                            description="",
                            url=href,
                            date_found=date.today().isoformat(),
                            source=f"company:{site['name'].lower().replace(' ', '_')}",
                        )
                    )
            except Exception as exc:
                logging.debug(f"[{site['name']}] Link parse error: {exc}")

        return jobs

    def _scrape_static(self, site: dict) -> list[JobListing]:
        import requests
        from bs4 import BeautifulSoup

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        }
        resp = requests.get(site["url"], headers=headers, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        jobs: list[JobListing] = []

        for link in soup.select(site["job_link_selector"]):
            try:
                title_sel = site.get("title_selector")
                loc_sel = site.get("location_selector")

                title_tag = link.select_one(title_sel) if title_sel else link
                loc_tag = link.select_one(loc_sel) if loc_sel else None

                title = (title_tag.get_text(strip=True) if title_tag else link.get_text(strip=True))
                location = loc_tag.get_text(strip=True) if loc_tag else ""
                href = link.get("href", "")
                if href and not href.startswith("http"):
                    href = urljoin(site["url"], href)

                if self._matches_search(title):
                    jobs.append(
                        JobListing(
                            title=title,
                            company=site["name"],
                            location=location,
                            description="",
                            url=href,
                            date_found=date.today().isoformat(),
                            source=f"company:{site['name'].lower().replace(' ', '_')}",
                        )
                    )
            except Exception as exc:
                logging.debug(f"[{site['name']}] Static link parse error: {exc}")

        return jobs

    def _matches_search(self, title: str) -> bool:
        t = title.lower()
        return any(st in t for st in self.search_titles) or any(kw in t for kw in self.search_keywords)

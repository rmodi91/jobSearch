from __future__ import annotations

import logging
import time
from datetime import date

from src.utils.dedup import JobListing

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CDP_URL = "http://localhost:9222"


class TrueUpScraper:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.cfg = config.get("trueup", {})
        self.rate_cfg = config.get("rate_limiting", {})
        self._use_cdp = config.get("_use_cdp", False)

    def scrape(self) -> list[JobListing]:
        if not self.cfg.get("enabled", True):
            logging.info("TrueUp scraper disabled in config")
            return []
        return self._scrape()

    def _scrape(self) -> list[JobListing]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logging.error("playwright not installed.")
            return []

        slow_mo = self.rate_cfg.get("playwright_slow_mo", 200)
        max_pages = self.cfg.get("max_pages", 10)
        jobs: list[JobListing] = []

        with sync_playwright() as pw:
            if self._use_cdp:
                # Connect to already-open Chrome window
                logging.info(f"[TrueUp] Connecting to Chrome at {CDP_URL}")
                browser = pw.chromium.connect_over_cdp(CDP_URL)
                context = browser.contexts[0]
                page = context.pages[0]
                logging.info(f"[TrueUp] Connected. Current page: {page.url}")
            else:
                # Launch fresh Chrome
                browser = pw.chromium.launch(
                    headless=False,
                    executable_path=CHROME_PATH,
                    slow_mo=slow_mo,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                page = context.new_page()
                page.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page.goto(self.cfg.get("base_url", "https://trueup.io/product"),
                          wait_until="domcontentloaded", timeout=45000)

                print("\n" + "="*60)
                print("  Chrome is open. Log in, navigate to job listings,")
                print("  apply filters. Scraping starts automatically...")
                print("="*60 + "\n")
                page.wait_for_selector('[data-slot="dialog-trigger"]', timeout=300000)
                time.sleep(3)

            logging.info("[TrueUp] Starting scrape — clicking 'Show More' to load all jobs...")

            # Keep clicking Show More until it disappears
            self._load_all_jobs(page, max_pages)

            # Now extract everything
            batch = self._extract_jobs(page)
            jobs.extend(batch)
            logging.info(f"[TrueUp] Extracted {len(jobs)} jobs total")

            if not self._use_cdp:
                browser.close()

        # Deduplicate by URL
        seen: set[str] = set()
        unique = [j for j in jobs if not (j.url in seen or seen.add(j.url))]  # type: ignore[func-returns-value]
        logging.info(f"[TrueUp] {len(unique)} unique jobs")
        return unique

    def _load_all_jobs(self, page, max_clicks: int = 10) -> None:
        """Click 'Show More' repeatedly until gone or max_clicks reached."""
        for i in range(max_clicks):
            # Scroll to bottom to reveal the button
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

            btn = None
            for selector in [
                "button:has-text('Show more')",
                "button:has-text('Show More')",
                "button:has-text('Load more')",
                "button:has-text('Load More')",
                "a:has-text('Next')",
                "[aria-label='Next page']",
            ]:
                candidate = page.query_selector(selector)
                if candidate and candidate.is_visible():
                    btn = candidate
                    break

            if not btn:
                logging.info(f"[TrueUp] No more 'Show More' button after {i} clicks")
                break

            logging.info(f"[TrueUp] Clicking 'Show More' ({i+1}/{max_clicks})...")
            btn.scroll_into_view_if_needed()
            btn.click()
            time.sleep(3)

        # Final scroll back to top so all cards render
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(2)
        # Scroll slowly through page to trigger virtual list rendering
        for y in range(0, 20000, 500):
            page.evaluate(f"window.scrollTo(0, {y})")
            time.sleep(0.3)
        page.evaluate("window.scrollTo(0, 0)")
        time.sleep(1)

    def _extract_jobs(self, page) -> list[JobListing]:
        jobs: list[JobListing] = []
        btns = page.query_selector_all('[data-slot="dialog-trigger"]')
        job_btns = [b for b in btns if len(b.inner_text().strip()) > 20]
        logging.info(f"[TrueUp] Found {len(job_btns)} job cards on page")

        for btn in job_btns:
            try:
                title = btn.inner_text().strip()
                card_text, company, slug, apply_url = self._parse_card(btn)
                location, salary, _ = self._parse_metadata(card_text)
                jobs.append(JobListing(
                    title=title,
                    company=company,
                    location=location,
                    description=f"Salary: {salary}" if salary else "",
                    url=apply_url,
                    date_found=date.today().isoformat(),
                    source="trueup",
                ))
            except Exception as exc:
                logging.debug(f"[TrueUp] Card parse error: {exc}")

        return jobs

    def _parse_card(self, btn) -> tuple[str, str, str, str]:
        card_html = btn.evaluate("""el => {
            let node = el;
            for (let i = 0; i < 8; i++) {
                node = node.parentElement;
                if (!node) break;
                const link = node.querySelector('a[href^="/co/"]');
                if (link) return {
                    text: node.innerText,
                    company: link.innerText.trim(),
                    slug: link.getAttribute('href').replace('/co/', '').replace('/jobs',''),
                    href: link.getAttribute('href')
                };
            }
            return {text: '', company: '', slug: '', href: ''};
        }""")
        company = card_html.get("company", "")
        slug = card_html.get("slug", "")
        card_text = card_html.get("text", "")
        apply_url = f"https://trueup.io/co/{slug}/jobs" if slug else "https://trueup.io/product"
        return card_text, company, slug, apply_url

    def _parse_metadata(self, card_text: str) -> tuple[str, str, str]:
        lines = [l.strip() for l in card_text.splitlines() if l.strip()]
        location = salary = date_posted = ""
        for line in lines:
            if not location and any(
                kw in line.upper()
                for kw in ["REMOTE", "GERMANY", "BERLIN", "USA", "UK", "HQ",
                           "HYBRID", ", CA", ", NY", ", WA", "INDIA", "SINGAPORE",
                           "LONDON", "PARIS", "AMSTERDAM", "BARCELONA"]
            ):
                location = line
            if not salary and "$" in line:
                salary = line
            if not date_posted and any(
                line.endswith(u) for u in ["day", "days", "hour", "hours", "week", "weeks"]
            ):
                date_posted = line
        return location, salary, date_posted

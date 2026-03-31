#!/usr/bin/env python3
"""
JobSearch CLI — Automated job discovery and CV tailoring.

Usage:
    python main.py run                          # full pipeline
    python main.py run --dry-run                # scrape only, no writes
    python main.py run --no-tailor              # write to sheet, skip CV tailoring
    python main.py run --sources linkedin       # limit to one source
    python main.py run --limit 10               # process at most 10 new jobs
    python main.py run --debug                  # headless=False for Playwright inspection
    python main.py schedule                     # start daily scheduler
    python main.py test-auth                    # verify all API credentials
"""
import sys
import click
from dotenv import load_dotenv

load_dotenv()


@click.group()
def cli() -> None:
    """JobSearch — Find matching jobs and tailor your CV automatically."""


@cli.command()
@click.option("--config", default="config.yaml", show_default=True, help="Path to config file")
@click.option(
    "--sources",
    multiple=True,
    type=click.Choice(["linkedin", "trueup", "company_sites"]),
    help="Limit to specific sources (default: all enabled in config)",
)
@click.option("--dry-run", is_flag=True, help="Scrape only — no sheet writes or CV tailoring")
@click.option("--no-tailor", is_flag=True, help="Write to sheet but skip CV tailoring")
@click.option("--limit", default=None, type=int, help="Process at most N new jobs")
@click.option("--debug", is_flag=True, help="Launch Playwright browsers in headed mode for inspection")
def run(config: str, sources: tuple, dry_run: bool, no_tailor: bool, limit: int | None, debug: bool) -> None:
    """Run the job search pipeline."""
    from src.pipeline import run_pipeline

    run_pipeline(
        config_path=config,
        sources=list(sources) if sources else None,
        dry_run=dry_run,
        skip_tailoring=no_tailor,
        limit=limit,
        debug=debug,
    )


@cli.command()
@click.option("--config", default="config.yaml", show_default=True)
def schedule(config: str) -> None:
    """Start the daily scheduler (runs pipeline once immediately, then daily at configured time)."""
    from scheduler import start_scheduler

    start_scheduler(config_path=config)


@cli.command("test-auth")
@click.option("--config", default="config.yaml", show_default=True)
def test_auth(config: str) -> None:
    """Verify that all API credentials are working."""
    import os
    from src.pipeline import load_config
    from src.services.google_sheets import GoogleSheetsService
    from src.services.google_docs import GoogleDocsService
    from src.services.cv_tailor import CVTailor

    cfg = load_config(config)
    all_ok = True

    click.echo("\nChecking credentials...\n")

    # Anthropic
    click.echo("  [1/3] Anthropic API key ... ", nl=False)
    try:
        tailor = CVTailor(cfg)
        ok = tailor.verify_connection()
        click.echo(click.style("OK", fg="green") if ok else click.style("FAIL", fg="red"))
        all_ok = all_ok and ok
    except Exception as exc:
        click.echo(click.style(f"FAIL ({exc})", fg="red"))
        all_ok = False

    # Google Sheets
    click.echo("  [2/3] Google Sheets ... ", nl=False)
    try:
        sheets = GoogleSheetsService(cfg)
        ok = sheets.verify_connection()
        click.echo(click.style("OK", fg="green") if ok else click.style("FAIL", fg="red"))
        all_ok = all_ok and ok
    except Exception as exc:
        click.echo(click.style(f"FAIL ({exc})", fg="red"))
        all_ok = False

    # Google Docs + Drive
    click.echo("  [3/3] Google Docs + Drive ... ", nl=False)
    try:
        docs = GoogleDocsService(cfg)
        ok = docs.verify_connection()
        click.echo(click.style("OK", fg="green") if ok else click.style("FAIL", fg="red"))
        all_ok = all_ok and ok
    except Exception as exc:
        click.echo(click.style(f"FAIL ({exc})", fg="red"))
        all_ok = False

    click.echo()
    if all_ok:
        click.echo(click.style("All credentials verified successfully.", fg="green"))
    else:
        click.echo(click.style("One or more credential checks failed. Check your .env and credentials/.", fg="red"))
        sys.exit(1)


@cli.command("open-browser")
def open_browser() -> None:
    """Open Chrome for manual login. Run 'scrape-trueup' after you're ready."""
    import subprocess, time
    print("\n" + "="*60)
    print("  Opening Chrome with remote debugging on port 9222...")
    print("  1. Log in to TrueUp")
    print("  2. Navigate to the job listings page")
    print("  3. Apply any filters (location, date, etc.)")
    print("  4. Run:  python main.py scrape-trueup")
    print("="*60 + "\n")
    subprocess.Popen([
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--remote-debugging-port=9222",
        "--no-first-run",
        "--no-default-browser-check",
        "https://trueup.io/product",
    ])
    print("Chrome is open. Come back here and run 'python main.py scrape-trueup' when ready.")


@cli.command("scrape-trueup")
@click.option("--config", default="config.yaml", show_default=True)
@click.option("--dry-run", is_flag=True, help="Print jobs without writing to sheet")
@click.option("--no-tailor", is_flag=True, help="Write to sheet but skip CV tailoring")
@click.option("--limit", default=None, type=int)
def scrape_trueup(config: str, dry_run: bool, no_tailor: bool, limit: int | None) -> None:
    """Connect to open Chrome window and scrape the current TrueUp job listing page."""
    from src.pipeline import load_config, run_pipeline
    from src.scrapers.trueup import TrueUpScraper
    from src.services.google_sheets import GoogleSheetsService
    from src.services.google_docs import GoogleDocsService
    from src.services.cv_tailor import CVTailor
    from src.utils.dedup import JobDeduplicator
    import logging, os

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    cfg = load_config(config)
    cfg["_use_cdp"] = True  # signal scraper to connect via CDP

    run_pipeline(
        config_path=config,
        sources=["trueup"],
        dry_run=dry_run,
        skip_tailoring=no_tailor,
        limit=limit,
        debug=False,
    )


if __name__ == "__main__":
    cli()

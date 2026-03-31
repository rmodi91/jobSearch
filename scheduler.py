"""
Daily job search scheduler.
Runs the pipeline immediately on start, then repeats daily at the configured time.
"""
from __future__ import annotations

import logging
import time

import schedule
import yaml
from dotenv import load_dotenv

load_dotenv()


def start_scheduler(config_path: str = "config.yaml") -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    with open(config_path) as fh:
        config = yaml.safe_load(fh)

    run_time: str = config.get("scheduling", {}).get("run_at", "08:00")
    logging.info(f"Scheduler started. Pipeline will run daily at {run_time} (local time).")

    from src.pipeline import run_pipeline

    def _job() -> None:
        logging.info("Scheduler: starting scheduled pipeline run")
        try:
            run_pipeline(config_path=config_path)
        except Exception as exc:
            logging.error(f"Scheduler: pipeline run failed: {exc}", exc_info=True)

    # Run once immediately on start, then schedule daily
    _job()
    schedule.every().day.at(run_time).do(_job)

    while True:
        schedule.run_pending()
        time.sleep(30)

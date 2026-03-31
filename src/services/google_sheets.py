from __future__ import annotations

import logging
import os

import gspread
from google.oauth2.service_account import Credentials

from src.utils.dedup import JobListing

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# Exact column order — DO NOT reorder without updating column index references below
SHEET_COLUMNS = [
    "Hash",             # A (col 1)  — internal dedup key; hide this column
    "Date Found",       # B (col 2)
    "Job Title",        # C (col 3)
    "Company",          # D (col 4)
    "Location",         # E (col 5)
    "Description",      # F (col 6)  — truncated to 500 chars
    "Apply URL",        # G (col 7)
    "Match Score",      # H (col 8)  — 0 until tailoring completes
    "Tailored CV Link", # I (col 9)  — Google Docs URL; empty until tailoring
    "Status",           # J (col 10) — New / Reviewing / Applying / Applied / Rejected / Offer
    "Notes",            # K (col 11) — free text
    "Source",           # L (col 12) — linkedin / trueup / company:stripe
]

COL_HASH = 1
COL_MATCH_SCORE = 8
COL_TAILORED_CV = 9


class GoogleSheetsService:
    def __init__(self, config: dict) -> None:
        creds = Credentials.from_service_account_file(
            os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"],
            scopes=SCOPES,
        )
        self._gc = gspread.authorize(creds)
        self._sheet_id = os.environ["JOBS_SHEET_ID"]
        self._ws: gspread.Worksheet | None = None

    @property
    def worksheet(self) -> gspread.Worksheet:
        if self._ws is None:
            sh = self._gc.open_by_key(self._sheet_id)
            try:
                self._ws = sh.worksheet("Jobs")
            except gspread.WorksheetNotFound:
                self._ws = sh.add_worksheet("Jobs", rows=2000, cols=len(SHEET_COLUMNS))
                self._ws.append_row(SHEET_COLUMNS)
                logging.info("Created new 'Jobs' worksheet with headers")
        return self._ws

    def get_all_job_hashes(self) -> set[str]:
        """Load all existing job hashes from column A (single API call)."""
        try:
            values = self.worksheet.col_values(COL_HASH)
            return set(v for v in values[1:] if v)  # skip header row
        except Exception as exc:
            logging.warning(f"Could not load existing hashes: {exc}")
            return set()

    def append_job(self, job: JobListing) -> None:
        """Write a new job row. match_score and tailored_cv_link filled in later."""
        row = [
            job.job_hash,
            job.date_found,
            job.title,
            job.company,
            job.location,
            job.description[:500],
            job.url,
            job.match_score,
            job.tailored_cv_link,
            job.status,
            job.notes,
            job.source,
        ]
        self.worksheet.append_row(row, value_input_option="USER_ENTERED")
        logging.debug(f"Appended row: {job.company} — {job.title}")

    def update_tailored_cv(self, job_hash: str, doc_url: str, match_score: int) -> None:
        """
        After CV tailoring completes, update the score and link columns.
        Finds the row by hash in column A.
        """
        try:
            cell = self.worksheet.find(job_hash, in_column=COL_HASH)
            if cell:
                self.worksheet.update_cell(cell.row, COL_MATCH_SCORE, match_score)
                self.worksheet.update_cell(cell.row, COL_TAILORED_CV, doc_url)
                logging.debug(f"Updated tailored CV for hash {job_hash}: score={match_score}")
            else:
                logging.warning(f"Hash {job_hash} not found in sheet — skipping update")
        except Exception as exc:
            logging.error(f"Failed to update tailored CV link for {job_hash}: {exc}")

    def verify_connection(self) -> bool:
        """Test that credentials and sheet ID are valid."""
        try:
            _ = self.worksheet
            return True
        except Exception as exc:
            logging.error(f"Google Sheets connection failed: {exc}")
            return False

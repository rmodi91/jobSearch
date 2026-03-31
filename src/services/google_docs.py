from __future__ import annotations

import logging
import os
from datetime import date

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from src.utils.dedup import JobListing

SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive",
]


class GoogleDocsService:
    def __init__(self, config: dict) -> None:
        creds = Credentials.from_service_account_file(
            os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"],
            scopes=SCOPES,
        )
        self._docs = build("docs", "v1", credentials=creds)
        self._drive = build("drive", "v3", credentials=creds)
        self._master_cv_id = os.environ["MASTER_CV_DOC_ID"]
        self._folder_id = os.environ["TAILORED_CVS_FOLDER_ID"]
        self._master_cv_cache: str | None = None

    def read_master_cv(self, force_refresh: bool = False) -> str:
        """
        Fetch and cache plain text from the master CV.
        Supports both native Google Docs and Word files (.docx) uploaded to Drive.
        """
        if self._master_cv_cache and not force_refresh:
            return self._master_cv_cache

        text = self._read_cv_text()
        self._master_cv_cache = text
        logging.info(f"Master CV loaded: {len(text)} characters")
        return text

    def _read_cv_text(self) -> str:
        # 1. Try native Google Docs API
        try:
            doc = self._docs.documents().get(documentId=self._master_cv_id).execute()
            return self._doc_to_text(doc)
        except Exception:
            pass

        # 2. Try Drive export as plain text (works for Google-converted Office files)
        try:
            content = (
                self._drive.files()
                .export(fileId=self._master_cv_id, mimeType="text/plain")
                .execute()
            )
            if isinstance(content, bytes):
                return content.decode("utf-8")
            return str(content)
        except Exception:
            pass

        # 3. Download raw .docx and parse with python-docx
        try:
            import io
            from docx import Document
            from googleapiclient.http import MediaIoBaseDownload

            request = self._drive.files().get_media(fileId=self._master_cv_id)
            buffer = io.BytesIO()
            downloader = MediaIoBaseDownload(buffer, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            buffer.seek(0)
            doc = Document(buffer)
            lines = [para.text for para in doc.paragraphs]
            return "\n".join(lines)
        except Exception as exc:
            raise RuntimeError(
                f"Could not read master CV (id={self._master_cv_id}). "
                f"Make sure the file is shared with the service account. Error: {exc}"
            )

    def _doc_to_text(self, doc: dict) -> str:
        """
        Recursively extract plain text from a Google Docs document.
        Handles paragraphs, tables, and nested structures.
        """
        lines: list[str] = []
        content = doc.get("body", {}).get("content", [])
        self._extract_content(content, lines)
        return "\n".join(lines)

    def _extract_content(self, content: list, lines: list[str]) -> None:
        for block in content:
            if "paragraph" in block:
                para_text = self._extract_paragraph(block["paragraph"])
                lines.append(para_text)
            elif "table" in block:
                # Walk table rows and cells for two-column CV layouts
                for row in block["table"].get("tableRows", []):
                    cell_texts: list[str] = []
                    for cell in row.get("tableCells", []):
                        cell_content: list[str] = []
                        self._extract_content(cell.get("content", []), cell_content)
                        cell_texts.append("  ".join(cell_content))
                    lines.append(" | ".join(cell_texts))

    def _extract_paragraph(self, para: dict) -> str:
        text = ""
        for element in para.get("elements", []):
            text_run = element.get("textRun")
            if text_run:
                text += text_run.get("content", "")
        return text.rstrip("\n")

    def create_tailored_cv(self, tailored_text: str, job: JobListing) -> str:
        """
        Create a new Google Doc in the tailored CVs folder.
        Returns the editable URL of the created document.

        Steps:
        1. Create blank doc with descriptive title
        2. Insert the tailored text via batchUpdate
        3. Move from root Drive to the configured folder
        """
        title = (
            f"CV — {job.company} — {job.title} — {date.today().isoformat()}"
        )

        # Step 1: Create blank document
        doc = self._docs.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]
        logging.info(f"Created doc '{title}' (id: {doc_id})")

        # Step 2: Insert tailored text at position 1 (start of blank doc)
        self._docs.documents().batchUpdate(
            documentId=doc_id,
            body={
                "requests": [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": tailored_text,
                        }
                    }
                ]
            },
        ).execute()

        # Step 3: Move to target folder (must supply both addParents + removeParents)
        file_meta = self._drive.files().get(
            fileId=doc_id, fields="parents"
        ).execute()
        previous_parents = ",".join(file_meta.get("parents", []))
        self._drive.files().update(
            fileId=doc_id,
            addParents=self._folder_id,
            removeParents=previous_parents,
            fields="id, parents",
        ).execute()

        url = f"https://docs.google.com/document/d/{doc_id}/edit"
        logging.info(f"Tailored CV available at: {url}")
        return url

    def verify_connection(self) -> bool:
        """Test that credentials, master CV, and folder are accessible."""
        try:
            # Check master CV is readable (native Doc or Drive-hosted Word file)
            self._read_cv_text()
            # Check tailored CVs folder is accessible
            self._drive.files().get(fileId=self._folder_id).execute()
            return True
        except Exception as exc:
            logging.error(f"Google Docs connection failed: {exc}")
            return False

from __future__ import annotations

import json
import logging
import os

from google import genai
from google.genai import types

from src.utils.dedup import JobListing

SYSTEM_PROMPT = """\
You are an expert career consultant and technical resume writer.
Your task is to tailor a candidate's master CV specifically for a job application.
Rules:
- Preserve ALL factual information. Never invent, fabricate, or exaggerate experience, skills, or accomplishments.
- Keep every job title, company name, and date exactly as written.
- Output clean, professional, ATS-optimized plain text ready to paste into a document.
- Do not add explanatory comments, headers like "TAILORED CV", or any text outside the CV itself.\
"""

USER_PROMPT_TEMPLATE = """\
## Job to Apply For

**Title**: {job_title}
**Company**: {company}
**Location**: {location}

### Full Job Description
{job_description}

---

## Candidate's Master CV

{master_cv}

---

## Your Task

1. **Analyse the job description** and identify:
   - Required hard skills (languages, frameworks, tools, platforms)
   - Desired soft skills and leadership signals
   - Key responsibilities and success metrics
   - Company values or culture signals

2. **Score the match** (1–10) based on how well the master CV aligns with this role.
   Consider: skill overlap, seniority level, domain experience, industry fit.

3. **Produce a tailored CV** by:
   - Reordering bullet points to lead with the most relevant accomplishments
   - Incorporating exact keywords from the job description naturally (for ATS matching)
   - Adjusting the professional summary / objective to speak directly to this role
   - Emphasising projects and experience that mirror the job's core requirements
   - De-emphasising unrelated experience (keep it, just move it lower)
   - Never removing any job, role, or date — only reorder and rephrase

## Required Output Format

Return a JSON object with exactly two keys:

```json
{{
  "match_score": <integer 1–10>,
  "tailored_cv": "<complete CV as a plain text string; use \\n for line breaks>"
}}
```

The `tailored_cv` field must contain the complete CV — not a diff, not a summary.
Respond with ONLY the JSON object. No other text before or after it.\
"""


class CVTailor:
    def __init__(self, config: dict) -> None:
        self._client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        cfg = config.get("cv_tailoring", {})
        self._model = cfg.get("model", "gemini-2.0-flash")

    def tailor(self, master_cv: str, job: JobListing) -> tuple[str, int]:
        """
        Returns (tailored_cv_text, match_score).
        Falls back to (master_cv, 0) on any error so the pipeline never crashes.
        """
        prompt = USER_PROMPT_TEMPLATE.format(
            job_title=job.title,
            company=job.company,
            location=job.location,
            job_description=job.description[:4000],
            master_cv=master_cv,
        )

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                ),
            )
            raw = response.text.strip()
            raw = self._strip_code_fence(raw)
            data = json.loads(raw)
            tailored = data.get("tailored_cv", "")
            score = int(data.get("match_score", 0))
            if not tailored:
                raise ValueError("Empty tailored_cv in response")
            logging.info(f"Tailored CV for {job.company} — {job.title}: score={score}")
            return tailored, score

        except json.JSONDecodeError as exc:
            logging.error(f"Gemini returned non-JSON for {job.company}/{job.title}: {exc}")
        except Exception as exc:
            logging.error(f"CV tailoring failed for {job.company}/{job.title}: {exc}")

        return master_cv, 0

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        if text.startswith("```"):
            parts = text.split("```")
            inner = parts[1] if len(parts) > 1 else text
            if inner.startswith("json"):
                inner = inner[4:]
            return inner.strip()
        return text

    def verify_connection(self) -> bool:
        try:
            self._client.models.generate_content(
                model=self._model,
                contents="ping",
            )
            return True
        except Exception as exc:
            # 429 = rate limited but key is valid
            if "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc):
                logging.warning(f"Gemini quota limit hit (key is valid): {exc}")
                return True
            logging.error(f"Gemini API check failed: {exc}")
            return False

import os
import logging
import base64
import json
import pathlib

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timezone
from google import genai
from google.genai import types
from dotenv import load_dotenv
from bs4 import BeautifulSoup

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from const import candidate_profile_system_instruction, jobs_emails_system_instruction, JOB_EMAIL_PATTERNS

# ---------------------------------------------------------------------------
# Logging setup — format includes timestamp and level so cron log is readable
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

load_dotenv()

# Google API scopes required: read Gmail + read/write Sheets
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/spreadsheets"]

# ---------------------------------------------------------------------------
# CHANGE THIS: set SPREADSHEET_ID in your .env file.
# Find it in the sheet URL: docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/
# ---------------------------------------------------------------------------
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    before_sleep=lambda retry_state: logging.warning(
        "Gemini request failed, retrying (attempt %d/3)...", retry_state.attempt_number
    ),
)
def call_gemini(client: genai.Client, candidate_profile: dict, formatted_emails: str) -> list:
    """
    Sends a batch of emails + candidate profile to Gemini for classification.
    Retries up to 3 times with exponential backoff on any exception.

    Returns a list of job classification dicts.
    """
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"""
            CANDIDATE PERSONA: {candidate_profile}
            EMAIL BATCH: {formatted_emails}
        """,
        config=types.GenerateContentConfig(
            system_instruction=jobs_emails_system_instruction,
            temperature=0.1,
            response_mime_type="application/json",
        ),
    )
    return json.loads(response.text)


def get_candidate_profile(client: genai.Client) -> dict:
    """
    Reads the candidate's resume PDF and sends it to Gemini to generate a
    structured Market Persona JSON. The result is cached in candidate_profile.json
    so it is only generated once unless the file is deleted.

    Returns the parsed profile as a dict.
    """
    resume_path = os.getenv("RESUME_PATH")
    if not resume_path:
        raise EnvironmentError("RESUME_PATH environment variable is not set.")

    resume_data = pathlib.Path(resume_path)
    if not resume_data.exists():
        raise FileNotFoundError(f"Resume file not found at: {resume_path}")

    logging.info("Generating candidate profile from resume: %s", resume_path)

    prompt = "Please analyze the following resume and generate the Market Persona according to your instructions."
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(
                    data=resume_data.read_bytes(),
                    mime_type="application/pdf",
                ),
                prompt,
            ],
            config=types.GenerateContentConfig(
                system_instruction=candidate_profile_system_instruction,
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
    except Exception as e:
        logging.error("Gemini API call failed while generating candidate profile: %s", e)
        raise

    profile = json.loads(response.text)
    with open("candidate_profile.json", "w") as f:
        json.dump(profile, f)

    logging.info("Candidate profile saved to candidate_profile.json")
    return profile


def update_existing_application(sheet, job: dict, table_values: list, company_applications: list) -> None:
    """
    Updates the status of an existing job application in the sheet.

    If multiple applications exist for the same company, it tries to match
    by job title and updates the status (column C) of the matching row.
    If only one application exists for that company, it updates its status directly.

    If multiple applications exist but no job title match is found, a warning
    is logged — this usually means the email did not include the job title.
    """
    try:
        if len(company_applications) > 1:
            # Multiple applications at the same company — need to match by job title
            value_changed = False
            for application in company_applications:
                table_row = application.get(job["company"])
                if job["job"] == table_values[table_row][0]:
                    # Column C (index 2) holds the status
                    sheet.values().update(
                        spreadsheetId=SPREADSHEET_ID,
                        range=f"Sheet1!C{table_row + 1}",
                        valueInputOption="RAW",
                        body={"values": [[job["status"]]]},
                    ).execute()
                    logging.info("Updated status for '%s' at '%s' to '%s'", job["job"], job["company"], job["status"])
                    value_changed = True

            if not value_changed:
                # Company found but no job title matched — email likely missing the role
                logging.warning(
                    "Could not find job role for '%s' (status: %s). Email may not contain job title.",
                    job["company"],
                    job["status"],
                )
        else:
            # Only one application at this company — update its status directly
            table_row = company_applications[0].get(job["company"])
            sheet.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"Sheet1!C{table_row + 1}",
                valueInputOption="RAW",
                body={"values": [[job["status"]]]},
            ).execute()
            logging.info("Updated status for '%s' to '%s'", job["company"], job["status"])

    except HttpError as e:
        logging.error("Failed to update application for '%s' at '%s': %s", job["job"], job["company"], e)
        raise


def append_new_application(sheet, job: dict) -> None:
    """
    Appends a new job application as a row in the sheet and expands the
    table range to include the new row.

    Expected job fields: job, company, status, est_salary, fit_score, logic
    """
    body = {
        "values": [[
            job["job"],
            job["company"],
            job["status"],
            job["est_salary"],
            job["fit_score"],
            job["logic"],
        ]]
    }

    try:
        sheet.values().append(
            spreadsheetId=SPREADSHEET_ID,
            range="A1",
            valueInputOption="USER_ENTERED",
            body=body,
        ).execute()
        logging.info("Appended new application: '%s' at '%s'", job["job"], job["company"])

        # Expand the sheet table range to include the newly appended row
        metadata_result = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
        table_prop = metadata_result["sheets"][0]["tables"][0]
        request_body = {
            "requests": [
                {
                    "updateTable": {
                        "table": {
                            "tableId": table_prop["tableId"],
                            "range": {
                                "startRowIndex": table_prop["range"]["startRowIndex"],
                                "endRowIndex": table_prop["range"]["endRowIndex"] + 1,
                                "startColumnIndex": table_prop["range"]["startColumnIndex"],
                                "endColumnIndex": table_prop["range"]["endColumnIndex"],
                            },
                        },
                        "fields": "range",
                    }
                }
            ]
        }
        sheet.batchUpdate(spreadsheetId=SPREADSHEET_ID, body=request_body).execute()

    except HttpError as e:
        logging.error("Failed to append application for '%s' at '%s': %s", job["job"], job["company"], e)
        raise


def get_gmail_messages(service, messages_id: list) -> list:
    """
    Fetches and decodes the body of each Gmail message, filters out
    marketing/unsubscribe emails, and returns them in chunks of 10
    for batched Gemini processing.

    Returns a list of chunks, where each chunk is a list of up to 10 email strings.
    Returns an empty list if no relevant messages are found.
    """
    messages_body = []

    for message_id in messages_id:
        try:
            msg = service.users().messages().get(userId="me", id=message_id["id"]).execute()
            payload = msg.get("payload", {})

            # Extract raw base64-encoded body — either directly or from parts
            if "data" in payload.get("body", {}):
                data = payload["body"]["data"]
            else:
                parts = payload.get("parts", [])
                if not parts:
                    logging.warning("Message %s has no body parts, skipping.", message_id["id"])
                    continue
                data = parts[0]["body"]["data"]

            body = base64.urlsafe_b64decode(data).decode("utf-8")

            # Skip emails that contain "unsubscribe" (marketing/newsletters),
            # unless the sender matches a known job platform AND the subject
            # matches one of its known application-related patterns.
            # Platform patterns are defined in const.py → JOB_EMAIL_PATTERNS.
            if "unsubscribe" in body.lower():
                headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
                sender = headers.get("from", "").lower()
                subject = headers.get("subject", "").lower()

                domain = next((d for d in JOB_EMAIL_PATTERNS if d in sender), None)
                if not domain or not any(p in subject for p in JOB_EMAIL_PATTERNS[domain]):
                    continue

            messages_body.append(BeautifulSoup(body, features="html.parser").get_text())

        except (KeyError, IndexError) as e:
            logging.warning("Could not parse message %s: %s", message_id["id"], e)
            continue

    if not messages_body:
        logging.info("No relevant messages found after filtering.")
        return []

    # Split into chunks of 10 to avoid sending too many emails in one Gemini request
    chunk_size = 10
    return [messages_body[i:i + chunk_size] for i in range(0, len(messages_body), chunk_size)]


def main():
    # ---------------------------------------------------------------------------
    # LOOKBACK_DAYS controls how far back to search for emails.
    # Set it in your .env file. Defaults to 1 day if not set.
    # ---------------------------------------------------------------------------
    if not SPREADSHEET_ID:
        raise EnvironmentError("SPREADSHEET_ID environment variable is not set.")

    lookback_days = int(os.getenv("LOOKBACK_DAYS", 1))
    now = datetime.now(timezone.utc).timestamp()
    lookback_start = int(now - (lookback_days * 24 * 60 * 60))
    lookback_end = int(now + (24 * 60 * 60))  # +1 day buffer to catch today's emails

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY environment variable is not set.")
    client = genai.Client(api_key=api_key)

    # ---------------------------------------------------------------------------
    # Google OAuth — credentials.json must be present (downloaded from Google
    # Cloud Console). token.json is auto-generated after first login and reused.
    # IMPORTANT: the OAuth flow opens a browser window, so authenticate locally
    # before running in Docker (token.json is then copied into the container).
    # ---------------------------------------------------------------------------
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logging.info("Refreshing expired Google credentials.")
            creds.refresh(Request())
        else:
            logging.info("No valid credentials found — starting OAuth flow.")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    # Load cached candidate profile or generate it from the resume
    if os.path.exists("candidate_profile.json") and os.path.getsize("candidate_profile.json") > 0:
        logging.info("Loading candidate profile from cache.")
        with open("candidate_profile.json", "r") as f:
            candidate_profile = json.load(f)
    else:
        logging.info("No cached candidate profile found — generating from resume.")
        candidate_profile = get_candidate_profile(client)

    try:
        gmail_service = build("gmail", "v1", credentials=creds)
        logging.info("Fetching emails from the last %d day(s).", lookback_days)

        results = gmail_service.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            q=f"after:{lookback_start} before:{lookback_end}",
        ).execute()

        messages_id = results.get("messages", [])

        if not messages_id:
            logging.info("No new messages found in the given time window.")
            return

        messages_chunks = get_gmail_messages(gmail_service, messages_id)

        if not messages_chunks:
            logging.info("No job-related messages to process.")
            return

        sheets_service = build("sheets", "v4", credentials=creds)
        sheet = sheets_service.spreadsheets()

        for chunks in messages_chunks:
            formatted_emails = "\n\n".join(
                [f"--- EMAIL {i + 1} ---\n{text}" for i, text in enumerate(chunks)]
            )

            # Send the email batch to Gemini — retries up to 3 times on failure
            try:
                jobs_responses = call_gemini(client, candidate_profile, formatted_emails)
            except Exception as e:
                logging.error("Gemini failed after all retries, skipping batch: %s", e)
                continue
            logging.info("Gemini returned %d job entries from this batch.", len(jobs_responses))

            for job in jobs_responses:
                if job["status"] == "Applied":
                    # Before appending, check if this job+company already exists in the sheet
                    # to avoid duplicates from multiple confirmation emails (e.g. LinkedIn)
                    existing = sheet.values().get(
                        spreadsheetId=SPREADSHEET_ID, range="Sheet1"
                    ).execute().get("values", [])
                    already_exists = any(
                        row[0] == job["job"] and row[1] == job["company"]
                        for row in existing if len(row) >= 2
                    )
                    if already_exists:
                        logging.warning(
                            "Duplicate detected — '%s' at '%s' already in sheet. Skipping.",
                            job["job"], job["company"]
                        )
                        continue
                    append_new_application(sheet, job)
                else:
                    # Status update — find the existing row and update it
                    result = sheet.values().get(
                        spreadsheetId=SPREADSHEET_ID, range="Sheet1"
                    ).execute()
                    table_values = result.get("values", [])

                    # Find all rows in the sheet that belong to this company
                    company_applications = [
                        {job["company"]: table_values.index(value)}
                        for value in table_values
                        if job["company"] in value
                    ]

                    if not company_applications:
                        # Company not in sheet — a non-Applied status for an unknown company
                        # means we never tracked this application, so skip it.
                        logging.warning(
                            "Received '%s' status for '%s' but company not found in sheet. Skipping.",
                            job["status"], job["company"]
                        )
                        continue

                    update_existing_application(sheet, job, table_values, company_applications)

    except HttpError as error:
        logging.error("Google API error: %s", error)
        raise


if __name__ == "__main__":
    main()

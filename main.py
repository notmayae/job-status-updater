import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, timedelta,timezone
from google import genai
from dotenv import load_dotenv
import base64
from bs4 import BeautifulSoup
from google.genai import types
import pathlib
import json
from const import candidate_profile_system_instruction, jobs_emails_system_instruction


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/spreadsheets"]

SAMPLE_SPREADSHEET_ID = "1TluyGAYp0lFlUn6Av64McGfeQ7AYf5JsZQ6XNCoMK_U"

yesterday = int(datetime.now(timezone.utc).timestamp() - (72*60*60))
tommorow = int(datetime.now(timezone.utc).timestamp() + (24*60*60))
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

def get_candidate_profile():
    resume_path=os.getenv("RESUME_PATH")
    resume_data = pathlib.Path(resume_path)

    prompt = "Please analyze the following resume and generate the Market Persona according to your instructions."
    response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=[
                types.Part.from_bytes(
                    data=resume_data.read_bytes(),
                    mime_type='application/pdf',
                ),
            prompt],
            config=types.GenerateContentConfig(system_instruction=candidate_profile_system_instruction,
               temperature=0.2, 
               response_mime_type="application/json")
            )
    with open("candidate_profile.json", 'w') as f:
        json.dump(json.loads(response.text),f)
    
    return response.text


def main():
  creds = None

  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)

  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)

    with open("token.json", "w") as token:
      token.write(creds.to_json())

  if os.path.exists("candidate_profile.json") and os.path.getsize('candidate_profile.json') != 0:
    with open("candidate_profile.json", 'r') as f:
        candidate_profile = json.load(f)
    
  else:
    candidate_profile = get_candidate_profile()


  try:
    # Call the Gmail API
    gmail_service = build("gmail", "v1", credentials=creds)
    results = (
            gmail_service.users().messages().list(userId="me", labelIds=["INBOX"], q=f"after:{yesterday} before:{tommorow}").execute()
        )
    messages_id = results.get("messages", [])
    if messages_id is not None:
        messages_chunks = get_gmail_messages(gmail_service, messages_id)
        api_responses =[]
        sheets_service = build("sheets", "v4", credentials=creds)
        sheet = sheets_service.spreadsheets()
        #result = (
        #    sheet.values().get(spreadsheetId=SAMPLE_SPREADSHEET_ID, range="Sheet1").execute()
        #)
        #values = result.get("values", [])
        
        for chunks in messages_chunks:
            formatted_emails = "\n\n".join([f"--- EMAIL {i+1} ---\n{text}" for i, text in enumerate(chunks)])
            response = client.models.generate_content(
                model="gemini-2.5-flash", 
                contents=f"""
                    CANDIDATE PERSONA: {candidate_profile}
                    EMAIL BATCH: {formatted_emails}
                            """, 
                config=types.GenerateContentConfig(system_instruction = jobs_emails_system_instruction,
                                                temperature=0.1, response_mime_type="application/json")

            )
            jobs_responses = json.loads(response.text)

            for job in jobs_responses:
                if job['status'] == 'Applied':
                    append_new_application(sheet, job)
                else:
                    result = (
                        sheet.values().get(spreadsheetId=SAMPLE_SPREADSHEET_ID, range="Sheet1").execute()
                    )
                    table_values = result.get("values", [])
                    company_applications = []
                    
                    for value in table_values:
                        if job['company'] in value:
                          company_applications.append({job['company']: table_values.index(value)})
                    if not company_applications:
                       append_new_application(sheet=sheet, job=job)
                       break
                    
                    if len(company_applications) > 1:
                        value_changed = False
                        for application in company_applications:
                            table_row = application.get(job['company']) 
                            if job['job'] == table_values[table_row][0]:
                                sheet.values().update(
                                    spreadsheetId=SAMPLE_SPREADSHEET_ID,
                                    range=f"Sheet1!A{table_row+1}",
                                    valueInputOption="RAW",
                                    body={"values": [[job['status']]]}
                                ).execute()
                                value_changed = True

                        if value_changed is False:
                          append_new_application(sheet,job)
                    else:
                        table_row = company_applications[0].get(job['company'])
                        sheet.values().update(
                            spreadsheetId=SAMPLE_SPREADSHEET_ID,
                            range=f"Sheet1!C{table_row+1}",
                            valueInputOption="RAW",
                            body={"values": [[job['status']]]}
                            ).execute()                    

            print(jobs_responses)

  except HttpError as error:
    print(f"An error occurred: {error}")

def append_new_application(sheet, job):
    body = {"values": [[
        job["job"],
        job["company"],
        job["status"],
        job["est_salary"],
        job["fit_score"],
        job["logic"]
    ]]}
    result = (            
        sheet.values().append(spreadsheetId=SAMPLE_SPREADSHEET_ID, range="A1", valueInputOption="USER_ENTERED", body=body).execute()
    )
    metadata_result = (sheet.get(spreadsheetId=SAMPLE_SPREADSHEET_ID).execute())
    table_prop = metadata_result["sheets"][0]['tables'][0]
    request_body = {
                        "requests": [
                            {
                                "updateTable": {
                                    "table": {
                                        "tableId": table_prop['tableId'], # Retrieve this from spreadsheet metadata
                                        "range": {         
                                            "startRowIndex": table_prop['range']['startRowIndex'],    # Top row (0-indexed)
                                            "endRowIndex": table_prop['range']['endRowIndex']+1,     # Bottom row (exclusive)
                                            "startColumnIndex": table_prop['range']['startColumnIndex'], # Left column (0-indexed)
                                            "endColumnIndex": table_prop['range']['endColumnIndex']    # Right column (exclusive)
                                        }
                                    },
                                    "fields": "range" # Specify that only the 'range' field should be updated
                                }
                            }
                        ]
                    }

    sheet.batchUpdate(
                        spreadsheetId=SAMPLE_SPREADSHEET_ID,
                        body=request_body
                    ).execute()

def get_gmail_messages(service, messages_id):
    if not messages_id:
        print("No messages found.")
        return
    messages_body =[]
    for message_id in messages_id:
        msg = (
            service.users().messages().get(userId="me", id=message_id["id"]).execute()
        )
        payload = msg.get("payload")
        if 'data' in payload.get("body", {}):
            data = payload["body"]["data"]
        else:
            parts = payload.get("parts", [])
            data = parts[0]["body"]["data"]

        body = base64.urlsafe_b64decode(data).decode("utf-8")
        if "unsubscribe" not in body:
            messages_body.append(BeautifulSoup(body,features="html.parser").get_text())

    chunk_size = 10
    messages_chunks = [messages_body[i:i + chunk_size] for i in range(0, len(messages_body), chunk_size)]
    return messages_chunks


if __name__ == "__main__":
  main()
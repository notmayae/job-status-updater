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

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

yesterday = int(datetime.now(timezone.utc).timestamp() - (24*60*60))
tommorow = int(datetime.now(timezone.utc).timestamp() + (24*60*60))
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)

def get_candidate_profile():
    resume_path=os.getenv("RESUME_PATH")
    resume_data = pathlib.Path(resume_path)
   # response=client.models.list(config={'page_size': 5, 'query_base': True})
   # print(response.page)
    prompt = "Please analyze the following resume and generate the Market Persona according to your instructions."
     
    response = client.models.generate_content(
            model="gemini-2.5-flash", 
            contents=[
                types.Part.from_bytes(
                    data=resume_data.read_bytes(),
                    mime_type='application/pdf',
                ),
            prompt],
            config=types.GenerateContentConfig(system_instruction="""
You are an expert Technical Recruiter and Compensation Analyst. Your task is to perform a deep analysis of a candidate's resume to build a structured 'Market Persona' in JSON format.

Analysis Dimensions:

Core Technical Domain: Identify the primary field and specific sub-niches of expertise.

Technical Proficiency Depth: Distinguish between 'knowledge of tools' and 'mastery of architectures.'

Effective Seniority Level: Determine seniority based on the complexity and impact of projects rather than years alone (Entry, Associate, Mid-Level, Senior, Staff/Lead).

Key Performance Indicators (KPIs): Extract the primary metrics the candidate has influenced.

Market Valuation (Baseline): Estimate a localized competitive baseline salary range based on current market rates for this specific seniority and domain. Specify the currency.

Notes: Any special strategic notes worth mentioning.

Constraint: Output ONLY a valid JSON object. Do not include markdown formatting or conversational text. You MUST use the following exact JSON schema:
{
"domain": "string",
"technical_depth": "string",
"seniority": "string",
"kpis": ["array of strings"],
"market_valuation": {"currency": "string", "baseline_range": "string", "basis": "string"},
"notes": "string",
"summary": "A concise, objective summary (max 200 words) of the candidate's market value. No names or contact info."
}""",
               temperature=0.2, 
               response_mime_type="application/json")
            )
    with open("candidate_profile.json", 'w') as f:
        json.dump(response.text,f)
    
    return response.text


def main():
  """Shows basic usage of the Gmail API.
  Lists the user's Gmail labels.
  """
  creds = None
  # The file token.json stores the user's access and refresh tokens, and is
  # created automatically when the authorization flow completes for the first
  # time.
  if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
  # If there are no (valid) credentials available, let the user log in.
  if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
      creds.refresh(Request())
    else:
      flow = InstalledAppFlow.from_client_secrets_file(
          "credentials.json", SCOPES
      )
      creds = flow.run_local_server(port=0)
    # Save the credentials for the next run
    with open("token.json", "w") as token:
      token.write(creds.to_json())

  if os.path.exists("candidate_profile.json") and os.path.getsize('candidate_profile.json') != 0:
    with open("candidate_profile.json", 'r') as f:
        candidate_profile = json.load(f)
    
  else:
    candidate_profile = get_candidate_profile()


  try:
    # Call the Gmail API
    service = build("gmail", "v1", credentials=creds)
    results = (
            service.users().messages().list(userId="me", labelIds=["INBOX"], q=f"after:{yesterday} before:{tommorow}").execute()
        )
    messages_id = results.get("messages", [])

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

    #for chunks in messages_chunks:
    #    response = client.models.generate_content(
    #        model="gemini-1.5-flash-preview", 
    #        contents="", 
    #        config=types.GenerateContentConfig(system_instruction = """
    #                                            Task: Extract job application status from emails.
    #                                            Format: Return ONLY a JSON array.
    #                                            Schema: [{"job": str, "company": str, "status": enum["Applied", "Interview/In Progress", "Rejected", "Other"], "Est. Salary Range": str}]
    #                                            Rule: If an email is not job-related ignore it.
    #                                            """
    #                                           temperature=0.1, response_mime_type="application/json")

    #    )
    #print(messages_body)

    #print("Messages:")
    #for message in messages:
    #        print(f'Message ID: {message["id"]}')
    #        msg = (
    #            service.users().messages().get(userId="me", id=message["id"]).execute()
    #        )
    #        print(f'  Subject: {msg["snippet"]}')

  except HttpError as error:
    # TODO(developer) - Handle errors from gmail API.
    print(f"An error occurred: {error}")


if __name__ == "__main__":
  main()
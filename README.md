# Job Status Updater

Automatically tracks your job applications by scanning your Gmail inbox and updating a Google Sheet. Powered by Gemini (Google AI) for email classification.

Runs on a cron schedule inside Docker (08:00, 13:00, 18:00 daily).

---

## How it works

1. Fetches recent emails from your Gmail inbox
2. Filters out marketing/newsletter emails — but preserves emails from known job platforms (e.g. LinkedIn application confirmations) based on sender domain and subject patterns defined in `const.py`
3. Sends the remaining emails + your resume profile to Gemini for classification
4. Gemini identifies job-related emails and returns: company, role, status, estimated salary, and fit score
5. New applications are appended to the Google Sheet; existing ones have their status updated

---

## Setup

### 1. Google Cloud — credentials.json

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the following APIs:
   - **Gmail API**
   - **Google Sheets API**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Choose **Desktop app** as the application type
6. Download the JSON file and rename it to `credentials.json`
7. Place `credentials.json` in the root of this project

### 2. Google Sheet

1. Create a new Google Sheet
2. Add a table with the following column headers (in order):
   | A | B | C | D | E | F |
   |---|---|---|---|---|---|
   | Job | Company | Status | Est. Salary | Fit Score | Logic |
3. Copy the Spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
4. Add it to your `.env` file (see below)

### 3. Gemini API key

1. Get an API key from [Google AI Studio](https://aistudio.google.com/app/apikey)
2. Add it to your `.env` file (see below)

### 4. Environment variables — .env

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
SPREADSHEET_ID=your_google_spreadsheet_id_here
LOOKBACK_DAYS=1

# For running locally: absolute path to your resume PDF on this machine
RESUME_PATH=/absolute/path/to/your/resume.pdf

# For running with Docker: path inside the container (keep this as-is)
# RESUME_PATH=/app/resume.pdf
# LOCAL_RESUME_PATH=/absolute/path/to/your/resume.pdf
```

| Variable | Required | Description |
|---|---|---|
| `GEMINI_API_KEY` | Yes | Your Gemini API key |
| `SPREADSHEET_ID` | Yes | Google Sheets ID from the sheet URL |
| `RESUME_PATH` | Yes | Path to your resume PDF (`/app/resume.pdf` when using Docker) |
| `LOCAL_RESUME_PATH` | Docker only | Absolute path to your resume on the host machine |
| `LOOKBACK_DAYS` | No | How many days back to search emails (default: 1) |

### 5. First-time authentication

The first run opens a browser window to authenticate with your Google account. This generates a `token.json` file which is reused for all future runs.

**Run locally first before using Docker:**

```bash
pip install -r requirements.txt
python main.py
```

Approve access in the browser when prompted. After that, `token.json` will be created.

---

## Running with Docker

Once you have `token.json`, set the Docker-specific vars in `.env`:

```env
RESUME_PATH=/app/resume.pdf
LOCAL_RESUME_PATH=/absolute/path/to/your/resume.pdf
```

Then build and start:

```bash
docker compose up -d --build
```

The container runs `main.py` automatically at **08:00, 13:00, and 18:00** every day.

Logs are written to `/var/log/cron.log` inside the container. To view them:

```bash
docker compose logs -f
# or directly inside the container:
docker exec <container_id> tail -f /var/log/cron.log
```

> **Note:** `token.json` is copied into the image at build time. If your token expires and can't be refreshed automatically, re-authenticate locally and rebuild the image with `docker compose up -d --build`.

---

## Project structure

```
.
├── main.py                  # Main script
├── const.py                 # Gemini system prompts
├── candidate_profile.json   # Cached resume analysis (auto-generated, do not commit)
├── credentials.json         # Google OAuth credentials (do not commit)
├── token.json               # Google OAuth token (do not commit)
├── requirements.txt         # Python dependencies
├── Dockerfile               # Container image definition
├── docker-compose.yml       # Compose setup with volume mounts and env file
├── .dockerignore            # Files excluded from the Docker image
└── .env                     # Environment variables (do not commit)
```

---

## Things to change for your own setup

| What | Where |
|---|---|
| Google Spreadsheet ID | `.env` → `SPREADSHEET_ID` |
| Email lookback window | `.env` → `LOOKBACK_DAYS` |
| Cron schedule | `Dockerfile` → the `echo "0 8,13,18 * * *..."` line |
| Gemini model | `main.py` → `model="gemini-2.5-flash"` (appears twice) |
| Resume classification prompt | `const.py` → `candidate_profile_system_instruction` |
| Email classification prompt | `const.py` → `jobs_emails_system_instruction` |
| Job platform email filters | `const.py` → `JOB_EMAIL_PATTERNS` (add sender domains and subject patterns) |

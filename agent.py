import os
import sys
import json
import base64
import sqlite3
import asyncio
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

# Force unbuffered output for Render logs
sys.stdout.reconfigure(line_buffering=True)
from openai import OpenAI
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from telegram import Bot
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()

# ─── Your Profile ────────────────────────────────────────────────────────────
MY_PROFILE = {
    "name": "Hemanth",
    "branch": "B.Tech AI&ML",
    "cgpa": 8.25,
    "batch": 2027,
    "degree": "B.Tech",
    "standing_arrears": False,
    "campus": "Vellore",  # or "Chennai"
}

# ─── Config ───────────────────────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CDC_SENDER_KEYWORD = "No Reply CDC Info"
DB_PATH = "cdc_emails.db"

NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

nim_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
)


# ─── Database ─────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS processed_emails (
            email_id TEXT PRIMARY KEY,
            company TEXT,
            eligible INTEGER,
            processed_at TEXT
        )"""
    )
    conn.commit()
    conn.close()


def is_processed(email_id: str) -> bool:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT email_id FROM processed_emails WHERE email_id = ?", (email_id,)
    ).fetchone()
    conn.close()
    return row is not None


def mark_processed(email_id: str, company: str, eligible: bool):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR IGNORE INTO processed_emails VALUES (?, ?, ?, ?)",
        (email_id, company, int(eligible), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


# ─── Gmail ────────────────────────────────────────────────────────────────────
def get_gmail_service():
    import base64, tempfile

    # Load from env vars (for cloud deployment) or local files (for local run)
    gmail_token = os.getenv("GMAIL_TOKEN")
    gmail_creds = os.getenv("GMAIL_CREDENTIALS")

    if gmail_token and gmail_creds:
        token_data = base64.b64decode(gmail_token).decode()
        creds = Credentials.from_authorized_user_info(json.loads(token_data), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def extract_email_body(message: dict) -> str:
    payload = message.get("payload", {})

    def decode(data: str) -> str:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

    # Multipart email
    if "parts" in payload:
        for part in payload["parts"]:
            mime = part.get("mimeType", "")
            if mime == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return decode(data)
            # Nested multipart
            if "parts" in part:
                for subpart in part["parts"]:
                    if subpart.get("mimeType") == "text/plain":
                        data = subpart.get("body", {}).get("data", "")
                        if data:
                            return decode(data)

    # Single part email
    data = payload.get("body", {}).get("data", "")
    if data:
        return decode(data)

    return ""


def fetch_new_cdc_emails() -> list[dict]:
    service = get_gmail_service()
    results = service.users().messages().list(
        userId="me",
        q=f'from:"{CDC_SENDER_KEYWORD}" newer_than:2d',
        maxResults=20,
    ).execute()

    messages = results.get("messages", [])
    emails = []

    for msg in messages:
        email_id = msg["id"]
        if is_processed(email_id):
            continue
        full = service.users().messages().get(
            userId="me", id=email_id, format="full"
        ).execute()
        body = extract_email_body(full)
        if body.strip():
            emails.append({"id": email_id, "body": body})

    return emails


# ─── Nemotron Parsing ─────────────────────────────────────────────────────────
PARSE_PROMPT = """You are parsing a VIT CDC (Career Development Centre) placement/internship email.

Extract the following fields and return ONLY valid JSON (no markdown, no explanation):

{{
  "company": "full company name",
  "category": "Super Dream / Dream / Normal / Internship",
  "eligible_branches": ["exact branch names listed"],
  "ai_ml_eligible": true/false,
  "min_cgpa": 0.0,
  "registration_deadline": "exact date and time string",
  "ctc_or_stipend": "CTC or stipend details",
  "date_of_visit": "interview/test date string",
  "job_location": "city",
  "designation": "role title",
  "no_arrears_required": true/false
}}

Rules:
- "ai_ml_eligible": true if branches include any of: "AI & ML", "AI&ML", "Artificial Intelligence", "Machine Learning", "CSE", "CSE/IT", "CSE related", "all branches", "all B.Tech". False only if branches are clearly unrelated (e.g., Mechanical/Civil/EEE only).
- "min_cgpa": numeric only. If percentage given (e.g., 75%), convert to CGPA (7.5). Take the CGPA for pursuing degree.
- "registration_deadline": include full date + time if available.

Email:
{email_body}"""


def parse_with_nemotron(email_body: str) -> dict:
    prompt = PARSE_PROMPT.format(email_body=email_body[:4000])  # cap tokens

    response = nim_client.chat.completions.create(
        model="nvidia/nemotron-3.5-bolt-e4b",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=600,
    )

    raw = response.choices[0].message.content.strip()

    # Strip markdown code fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.split("```")[0].strip()

    return json.loads(raw)


# ─── Eligibility Check ────────────────────────────────────────────────────────
def check_eligibility(parsed: dict) -> tuple[bool, list[str]]:
    reasons = []

    if not parsed.get("ai_ml_eligible", False):
        branches = ", ".join(parsed.get("eligible_branches", []))
        reasons.append(f"Branch not eligible — Required: {branches}")

    min_cgpa = float(parsed.get("min_cgpa") or 0)
    if min_cgpa > 0 and MY_PROFILE["cgpa"] < min_cgpa:
        reasons.append(
            f"CGPA too low — Yours: {MY_PROFILE['cgpa']}, Required: {min_cgpa}"
        )

    if parsed.get("no_arrears_required") and MY_PROFILE["standing_arrears"]:
        reasons.append("You have standing arrears")

    eligible = len(reasons) == 0
    return eligible, reasons


# ─── Telegram Notification ────────────────────────────────────────────────────
def build_message(parsed: dict, eligible: bool, reasons: list[str]) -> str:
    if eligible:
        header = "✅ YOU ARE ELIGIBLE — REGISTER NOW!"
    else:
        header = "❌ Not Eligible"

    msg = f"""🎓 <b>VIT CDC PLACEMENT ALERT</b>
{header}

🏢 <b>Company:</b> {parsed.get('company', 'N/A')}
📋 <b>Category:</b> {parsed.get('category', 'N/A')}
👔 <b>Role:</b> {parsed.get('designation', 'N/A')}
💰 <b>CTC / Stipend:</b> {parsed.get('ctc_or_stipend', 'N/A')}
📍 <b>Location:</b> {parsed.get('job_location', 'N/A')}
📅 <b>Interview / Test:</b> {parsed.get('date_of_visit', 'N/A')}
⏰ <b>Registration Deadline:</b> {parsed.get('registration_deadline', 'N/A')}
🎓 <b>Min CGPA Required:</b> {parsed.get('min_cgpa', 'N/A')}"""

    if not eligible:
        msg += f"\n\n❌ <b>Why not eligible:</b>\n" + "\n".join(f"• {r}" for r in reasons)
    else:
        msg += "\n\n⚡ <b>Go register on NEO PAT portal immediately!</b>"

    return msg


async def send_notification(text: str):
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text, parse_mode="HTML")


# ─── Main Job ─────────────────────────────────────────────────────────────────
def run_check():
    now = datetime.now()
    print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Checking CDC emails...")

    try:
        emails = fetch_new_cdc_emails()
    except Exception as e:
        print(f"  Gmail fetch error: {e}")
        return

    if not emails:
        print("  No new CDC emails.")
        return

    for email in emails:
        email_id = email["id"]
        body = email["body"]

        try:
            parsed = parse_with_nemotron(body)
            eligible, reasons = check_eligibility(parsed)
            message = build_message(parsed, eligible, reasons)

            asyncio.run(send_notification(message))
            mark_processed(email_id, parsed.get("company", "unknown"), eligible)

            status = "ELIGIBLE" if eligible else "not eligible"
            print(f"  [{status}] {parsed.get('company')} — deadline: {parsed.get('registration_deadline')}")

        except json.JSONDecodeError as e:
            print(f"  JSON parse error for email {email_id}: {e}")
        except Exception as e:
            print(f"  Error processing email {email_id}: {e}")


# ─── Scheduler ────────────────────────────────────────────────────────────────
def start_health_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"CDC Agent running")
        def log_message(self, *args):
            pass
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Health server on port {port}")


def main():
    init_db()
    start_health_server()
    print("=" * 50)
    print("VIT CDC Email Agent")
    print(f"Profile: {MY_PROFILE['name']} | {MY_PROFILE['branch']} | CGPA {MY_PROFILE['cgpa']}")
    print("Schedule: every 10 min, 9 AM – midnight")
    print("=" * 50)

    # Run once immediately on start
    run_check()

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(
        run_check,
        trigger="cron",
        hour="9-23",
        minute="*/10",
        id="cdc_check",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Agent stopped.")


if __name__ == "__main__":
    main()

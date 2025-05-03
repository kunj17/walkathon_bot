import os
import base64
import json
import subprocess
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Load and decode service account JSON
creds_b64 = os.getenv['GOOGLE_SERVICE_ACCOUNT_JSON']
creds_json = base64.b64decode(creds_b64).decode()
with open("temp_creds.json", "w") as f:
    f.write(creds_json)

# Authenticate
scopes = ['https://www.googleapis.com/auth/spreadsheets.readonly']
creds = service_account.Credentials.from_service_account_file("temp_creds.json", scopes=scopes)
service = build('sheets', 'v4', credentials=creds)

# Read Sheet
sheet_id = os.getenv['GOOGLE_SHEET_ID']
range_name = "01-01-2025 to 05-02-2025"
result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range=range_name).execute()
values = result.get("values", [])

headers = values[0]
data = [dict(zip(headers, row)) for row in values[1:]]

# Save JSON
with open("data.json", "w") as f:
    json.dump(data, f, indent=2)

# Encrypt with GPG
gpg_input = os.getenv['GPG_PRIVATE_KEY']
with open("private.key", "w") as f:
    f.write(gpg_input)

subprocess.run(["gpg", "--batch", "--import", "private.key"])
subprocess.run([
    "gpg", "--batch", "--yes", "--passphrase", os.getenv["GPG_PASSPHRASE"],
    "-o", "encrypted_data.json.gpg", "-c", "data.json"
])


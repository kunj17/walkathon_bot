import os
import json
import time
import base64
import asyncio
from dotenv import load_dotenv
from telegram import Update
from decrypt_utils import decrypt_and_load_json
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)
import gspread
import gnupg
from google.oauth2 import service_account
from googleapiclient.discovery import build

# === Load env ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
SHEET_NAME = "01-01-2025 to 05-02-2025"
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

# === Google Sheets Setup ===
print("kunj checking0 - " + SERVICE_ACCOUNT_JSON_RAW)
SERVICE_ACCOUNT_JSON = base64.b64decode(SERVICE_ACCOUNT_JSON_RAW)
print("kunj checking - " + SERVICE_ACCOUNT_JSON)

creds = service_account.Credentials.from_service_account_info(
    json.loads(SERVICE_ACCOUNT_JSON),
    scopes=["https://www.googleapis.com/auth/spreadsheets"]
)
sheets_service = build('sheets', 'v4', credentials=creds)

# === Load decrypted registration data ===
registration_data = decrypt_and_load_json(GPG_PASSPHRASE)

# === Globals ===
user_state = {}  # chat_id -> dict(state)
SESSION_TTL = 30  # seconds
MAX_MSG_LENGTH = 4000  # Telegram safe limit

def extract_shirt_info(row):
    sizes = ["SM", "MD", "LG", "XL", "XXL", "Y-LG", "Y-MD", "Y-SM", "Y-XS"]
    shirt_counts = {}
    for size in sizes:
        count = row.get(size)
        try:
            count = int(count) if count is not None else 0
        except ValueError:
            count = 0
        if count > 0:
            shirt_counts[size] = count
    return shirt_counts

def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees', '?')
    city = row.get('City', 'Unknown')
    family = row.get('Additional Family Members', 'None').strip()
    bag_no = row.get('Bag No.', 'N/A')
    shirts = extract_shirt_info(row)
    total_shirts = sum(shirts.values())

    response = f"""✅ *{full_name}* is registered.
📍 *City:* {city}
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family Members:*
{family if family else 'None'}"""

    if shirts:
        response += "\n\n👕 *T-Shirts Ordered:*\n"
        for size, count in shirts.items():
            response += f"- {size}: {count}\n"
        response += f"\n📦 *Total T-Shirts:* {total_shirts}"
    else:
        response += "\n\n👕 *T-Shirts Ordered:* None"

    response += f"\n🎒 *Bag No.:* {bag_no}"

    if entry['via_family']:
        response += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"

    return response

def prefix_match(name, city, data):
    name_lower = name.lower()
    direct = []
    family = []

    for row in data:
        r_city = row.get('City', '').lower()
        r_fname = row.get('Registrant First Name', '').lower()
        r_lname = row.get('Registrant Last Name', '').lower()
        full_name = f"{r_fname} {r_lname}"

        if city and not r_city.startswith(city.lower()):
            continue

        if (
            r_fname.startswith(name_lower)
            or r_lname.startswith(name_lower)
            or full_name.startswith(name_lower)
        ):
            direct.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower().startswith(name_lower):
                family.append({'row': row, 'via_family': True, 'matched_family': line.strip()})
                break

    return sorted(direct + family, key=lambda x: x['row'].get('Registrant First Name', ''))

async def fetch_latest_data():
    try:
        range_name = f"'{SHEET_NAME}'!A1:Z1000"
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=range_name
        ).execute()
        values = result.get("values", [])
        headers = values[0]
        return [dict(zip(headers, row)) for row in values[1:]]
    except Exception as e:
        print(f"❌ Failed to fetch live sheet: {e}")
        return []

def update_pickup_column(row, value):
    try:
        all_data = sheets_service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID,
            range=f"'{SHEET_NAME}'!A1:Z1000"
        ).execute().get("values", [])

        headers = all_data[0]
        for idx, r in enumerate(all_data[1:], start=2):
            record = dict(zip(headers, r))
            if (
                record.get('Registrant First Name') == row.get('Registrant First Name')
                and record.get('Registrant Last Name') == row.get('Registrant Last Name')
                and record.get('City') == row.get('City')
            ):
                col_index = headers.index("Pickup") + 1
                sheets_service.spreadsheets().values().update(
                    spreadsheetId=SHEET_ID,
                    range=f"'{SHEET_NAME}'!{chr(64 + col_index)}{idx}",
                    valueInputOption="RAW",
                    body={"values": [[value]]}
                ).execute()
                return True
    except Exception as e:
        print(f"❌ Error updating sheet: {e}")
    return False

async def send_split_message(text, update):
    lines = text.strip().split('\n')
    chunks = []
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 < MAX_MSG_LENGTH:
            current += line + "\n"
        else:
            chunks.append(current.strip())
            current = line + "\n"
    if current:
        chunks.append(current.strip())

    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode='Markdown')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use `b` to check registration, `p` to mark pickup, or `p remove` to undo.\nType `/help` or `/format` to view all supported formats."
    )

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """🛠️ *Available Commands*

📘 *Check Registration* (`b ...`)
- `b FirstName City`
- `b FirstName LastName City`
- `b LastName City`
- `b FirstName` *(any city)*
- `b LastName` *(any city)*
- `b Kun add` *(partial match)*
- `b kunj\\naddison` *(multi-line input)*
- `b format` *(show this)*

📦 *Mark Pickup* (`p ...`)
- `p FirstName City`
- `p FirstName LastName City`
- `p LastName City`
- `p FirstName`
- `p LastName`
- `p Kun add`
- `p kunj\\naddison`

🚫 *Undo Pickup* (`p remove ...`)
- Same formats as above, just add `remove`
  - Example: `p remove Kunj Patel Addison`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace('\n', ' ')
    chat_id = update.effective_chat.id
    now = time.time()

    if chat_id in user_state and now - user_state[chat_id].get('timestamp', 0) > SESSION_TTL:
        del user_state[chat_id]

    state = user_state.get(chat_id, {})

    if text.lower() in ["b format", "/format", "/help"]:
        await show_help(update, context)
        return

    if 'awaiting_choice' in state and text.isdigit():
        idx = int(text) - 1
        matches = state.get('matches', [])
        if 0 <= idx < len(matches):
            await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
        del user_state[chat_id]
        return

    if 'awaiting_pickup' in state and text.isdigit():
        idx = int(text) - 1
        matches = state.get('matches', [])
        is_remove = state.get('is_remove', False)
        if 0 <= idx < len(matches):
            value = "" if is_remove else "Yes"
            update_pickup_column(matches[idx]['row'], value)
            name = matches[idx]['row'].get('Registrant First Name', '')
            await update.message.reply_text(
                f"✅ *{name}* {'removed from' if is_remove else 'marked for'} pickup.",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("❗ Invalid number.")
        del user_state[chat_id]
        return

    if text.lower().startswith("p "):
        is_remove = text.lower().startswith("p remove")
        query = text[9:] if is_remove else text[2:]
        tokens = query.strip().split()
        name, city = (tokens[0], None) if len(tokens) == 1 else (" ".join(tokens[:-1]), tokens[-1])

        print(f"🧪 Parsed → name: '{name}' | city: '{city}' | remove: {is_remove}")

        matches = prefix_match(name, city, registration_data)
        if not matches:
            await update.message.reply_text(
                f"❌ No matches found for *{name}* in *{city or 'any city'}*.",
                parse_mode='Markdown'
            )
            return

        value = "" if is_remove else "Yes"

        if len(matches) == 1:
            update_pickup_column(matches[0]['row'], value)
            await update.message.reply_text(
                f"✅ *{name}* {'removed from' if is_remove else 'marked for'} pickup.",
                parse_mode='Markdown'
            )
        else:
            reply = f"🔎 *Found {len(matches)} possible matches:*\n\n"
            for i, m in enumerate(matches, 1):
                r = m['row']
                full = f"{r.get('Registrant First Name', '')} {r.get('Registrant Last Name', '')}"
                city_name = r.get('City', '?')
                note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
                reply += f"{i}. *{full}* – {city_name}{note}\n"
            reply += f"\n✉️ Reply with the number to {'remove' if is_remove else 'mark'} pickup."

            await send_split_message(reply, update)
            user_state[chat_id] = {
                'awaiting_pickup': True,
                'matches': matches,
                'timestamp': now,
                'is_remove': is_remove
            }

            async def timeout_clear():
                await asyncio.sleep(SESSION_TTL)
                if user_state.get(chat_id, {}).get('timestamp') == now:
                    await context.bot.send_message(chat_id, "⏳ Timeout. Send a new query.")
                    user_state.pop(chat_id, None)

            asyncio.create_task(timeout_clear())
        return

    if not text.lower().startswith("b "):
        return

    tokens = text[2:].strip().split()
    name, city = (tokens[0], None) if len(tokens) == 1 else (" ".join(tokens[:-1]), tokens[-1])
    matches = prefix_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(
            f"❌ No matches found for *{name}* in *{city or 'any city'}*.",
            parse_mode='Markdown'
        )
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"🔎 *Found {len(matches)} possible matches:*\n\n"
        for i, m in enumerate(matches, 1):
            r = m['row']
            full = f"{r.get('Registrant First Name', '')} {r.get('Registrant Last Name', '')}"
            city_name = r.get('City', '?')
            attendees = r.get('Attendees', '?')
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"{i}. *{full}* — {attendees} attendees – {city_name}{note}\n"
        reply += "\n✉️ *Reply with the number to see full details.*"

        await send_split_message(reply, update)
        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_clear():
            await asyncio.sleep(SESSION_TTL)
            if user_state.get(chat_id, {}).get('timestamp') == now:
                await context.bot.send_message(chat_id, "⏳ Timeout. Send a new query.")
                user_state.pop(chat_id, None)

        asyncio.create_task(timeout_clear())

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("help", show_help))
app.add_handler(CommandHandler("format", show_help))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

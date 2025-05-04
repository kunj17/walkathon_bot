import os
import json
import time
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)
from decrypt_utils import decrypt_file

# === Load env + decrypt data ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Globals ===
user_state = {}  # chat_id -> dict(state)
SESSION_TTL = 30  # seconds
MAX_MSG_LENGTH = 4000  # Telegram safe limit

# === Matching Logic ===
def prefix_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower() if city else None
    direct = []
    family = []

    for row in data:
        r_city = row.get('City', '').lower()
        if city_lower and not r_city.startswith(city_lower):
            continue

        r_fname = row.get('Registrant First Name', '').lower()
        r_lname = row.get('Registrant Last Name', '').lower()
        full_name = f"{r_fname} {r_lname}"

        if (
            r_fname.startswith(name_lower)
            or r_lname.startswith(name_lower)
            or full_name.startswith(name_lower)
        ):
            direct.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        matched_line = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower().startswith(name_lower):
                matched_line = line.strip()
                break
        if matched_line:
            family.append({'row': row, 'via_family': True, 'matched_family': matched_line})

    # Sort results by first name
    sorted_results = sorted(direct if direct else family, key=lambda x: x['row'].get('Registrant First Name', '').lower())
    return sorted_results

# === Format Result Entry ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees', '?')
    family = row.get('Additional Family Members', 'None').strip()

    msg = f"""✅ *{full_name}* is registered.
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family Members:*
{family if family else 'None'}"""
    if entry['via_family']:
        msg += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"
    return msg

# === Smart Chunked Message Sender ===
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

# === Command: /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use `b FirstName City` to check registration.\nType `b format` to see all supported input styles."
    )

# === Command: b format ===
async def show_formats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    formats = """📘 *Supported Search Formats:*

1. `b FirstName City`  
2. `b FirstName LastName City`  
3. `b LastName City`  
4. `b FirstName` *(shows from all cities)*  
5. `b LastName` *(shows from all cities)*  

You can also type like:
- `b Kun add` → Kunj Patel from Addison
- `b kunj\naddison` → Will still work!
"""
    await update.message.reply_text(formats, parse_mode='Markdown')

# === Main Message Handler ===
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace('\n', ' ')
    chat_id = update.effective_chat.id
    now = time.time()

    # Session cleanup
    if chat_id in user_state and now - user_state[chat_id].get('timestamp', 0) > SESSION_TTL:
        del user_state[chat_id]

    state = user_state.get(chat_id, {})

    # Handle "b format"
    if text.lower() == "b format":
        await show_formats(update, context)
        return

    # Handle number reply
    if 'awaiting_choice' in state and text.isdigit():
        idx = int(text) - 1
        matches = state.get('matches', [])
        if 0 <= idx < len(matches):
            print(f"✅ User selected #{idx+1} from previous results")
            await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
            del user_state[chat_id]
        else:
            print(f"❌ Invalid number: {text}")
            await update.message.reply_text("❗ Invalid number.")
        return

    # Only respond to b-queries
    if not text.lower().startswith("b "):
        return

    tokens = text[2:].strip().split()
    if not tokens:
        await update.message.reply_text("❗ Please use: `b FirstName City` or `b LastName City`")
        return

    if len(tokens) == 1:
        name, city = tokens[0], None
    else:
        name, city = " ".join(tokens[:-1]), tokens[-1]

    print(f"🔍 Query received: name='{name}' | city='{city}'")
    matches = prefix_match(name, city, registration_data)

    if not matches:
        print("❌ No match found.")
        await update.message.reply_text(
            f"❌ No matches found for *{name}* in *{city or 'any city'}*.",
            parse_mode='Markdown'
        )
        return

    print(f"✅ Match found: {len(matches)} result(s)")
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
            current = user_state.get(chat_id)
            if current and current.get('timestamp') == now and current.get('awaiting_choice'):
                await context.bot.send_message(chat_id, "⏳ Timeout. Send a new query like `b Patel Frisco` to continue.")
                del user_state[chat_id]

        asyncio.create_task(timeout_clear())

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

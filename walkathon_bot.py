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
from fuzzywuzzy import fuzz
from decrypt_utils import decrypt_file

# === Load env + decrypt data ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Session cache with TTL ===
user_state = {}  # chat_id -> dict(state, matches, timestamp)
SESSION_TTL = 10  # seconds

# === Matching Logic ===
def fuzzy_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    direct_exact, family_matches, fallback_suggestions = [], [], []

    for row in data:
        r_city = row.get('City', '').lower()
        if city_lower not in r_city:
            continue

        first = row.get('Registrant First Name', '')
        last = row.get('Registrant Last Name', '')
        full_name = f"{first} {last}".strip().lower()

        # Priority 1: Exact full name match
        if name_lower == full_name:
            direct_exact.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        # Priority 2: Exact family member match
        matched_family = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if name_lower == line.strip().lower().split(" Gender")[0]:
                matched_family = line.strip()
                break
        if matched_family:
            family_matches.append({'row': row, 'via_family': True, 'matched_family': matched_family})
            continue

        # Fallback: suggest nearby names
        if fuzz.partial_ratio(name_lower, full_name) >= 75:
            fallback_suggestions.append({'row': row, 'via_family': False, 'matched_family': None})

    if direct_exact:
        return direct_exact
    elif family_matches:
        return family_matches
    elif fallback_suggestions:
        return fallback_suggestions
    else:
        return []

# === Format Result ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".strip()
    attendees = row.get('Attendees') or '?'
    family = row.get('Additional Family Members', 'None').strip()

    result = f"""✅ *{full_name}* is registered."""
    if entry['via_family']:
        result += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"
    result += f"\n👥 *Attendees:* {attendees}"
    result += f"\n👨‍👩‍👧 *Family:*\n{family if family else 'None'}"
    return result

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Welcome! Use `b FullName City` to check registration.\nExample: `b Kunj Addison`")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if not text.lower().startswith("b "):
        return

    text = text[2:].strip()

    # Check if we’re in a session expecting a number
    state = user_state.get(chat_id, {})
    if state and 'awaiting_choice' in state:
        if text.isdigit():
            idx = int(text) - 1
            if 0 <= idx < len(state['matches']):
                await update.message.reply_text(format_entry(state['matches'][idx]), parse_mode='Markdown')
                del user_state[chat_id]
            else:
                await update.message.reply_text("❗ Invalid number.")
            return
        elif len(text.split()) >= 2:
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Invalid response. Send number or try again like `b Patel Frisco`.")
            return

    tokens = text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Format: `b FullName City`\nExample: `b Kunj Addison`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"🤖 No matches found for *{name}* in *{city}*.", parse_mode='Markdown')
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        msg = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, match in enumerate(matches, 1):
            row = match['row']
            registrant = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".strip()
            attendees = row.get('Attendees') or '?'
            note = f" (_via family: {match['matched_family']}_)" if match['via_family'] else ""
            msg += f"{i}. {registrant} – {attendees} attendees{note}\n"
        msg += "\nPlease reply with the number to view details."
        await update.message.reply_text(msg, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout():
            await asyncio.sleep(SESSION_TTL)
            if user_state.get(chat_id, {}).get('timestamp') == now:
                await context.bot.send_message(chat_id, "⏳ Waited 10 seconds... no reply received.\n🤖 I'm moving on. Send a new query like `b Patel Frisco`!")
                user_state.pop(chat_id, None)

        asyncio.create_task(timeout())

# === Init Bot ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

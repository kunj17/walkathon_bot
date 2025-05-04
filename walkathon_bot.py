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
SESSION_TTL = 15  # seconds

# === Matching Logic ===
def fuzzy_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    direct_strong = []
    family_matches = []

    for row in data:
        r_city = row.get('City', '').lower()
        if city_lower not in r_city:
            continue

        # Direct full name match
        full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".lower()
        if fuzz.token_set_ratio(name_lower, full_name) >= 85:
            direct_strong.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        # Check family members
        matched_family = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if fuzz.token_set_ratio(name_lower, line.lower()) >= 85:
                matched_family = line.strip()
                break
        if matched_family:
            family_matches.append({'row': row, 'via_family': True, 'matched_family': matched_family})

    if direct_strong:
        return direct_strong
    if family_matches:
        return family_matches
    return []

# === Format Result ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or '?'
    family = row.get('Additional Family Members', 'None').strip()

    response = f"""✔️ *{full_name}* is registered.
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family:*\n{family if family else 'None'}"""

    if entry['via_family']:
        response += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"

    return response

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use `b FullName City` to check registration.\nExample: `b Kunj Addison`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if not text.lower().startswith("b "):
        return

    text = text[2:].strip()

    if chat_id in user_state:
        state = user_state[chat_id]
        if 'timestamp' in state and now - state['timestamp'] > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    if 'awaiting_choice' in state:
        if text.isdigit():
            idx = int(text) - 1
            matches = state.get('matches', [])
            if 0 <= idx < len(matches):
                await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
                del user_state[chat_id]
                return
            else:
                await update.message.reply_text("❗ Invalid number. Please try again.")
                return
        elif len(text.split()) >= 2:
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Please reply with a number from the list or a new query.")
            return

    tokens = text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Please use Format: `b LastName City` or `b FullName City`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(
            f"❌ Sorry, no registration found for *{name}* in *{city}*.\n"
            "🔍 Please double-check the spelling or try a different family member name.",
            parse_mode='Markdown'
        )
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"🔎 Found *{len(matches)}* possible registrations:\n\n"
        for i, m in enumerate(matches, 1):
            r = m['row']
            full = f"{r.get('Registrant First Name', '')} {r.get('Registrant Last Name', '')}"
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"{i}. {full} – {r.get('Attendees') or '?'} attendees{note}\n"
        reply += "\n✉️ *Please reply with the number to view full details.*"
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_warning():
            await asyncio.sleep(SESSION_TTL)
            state = user_state.get(chat_id)
            if state and state.get('timestamp') == now and state.get('awaiting_choice'):
                await context.bot.send_message(
                    chat_id,
                    "⏳ Waited 15 seconds... no reply received.\n🤖 I'm moving on. Send a new query like `b Patel Frisco`!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === Init Bot ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

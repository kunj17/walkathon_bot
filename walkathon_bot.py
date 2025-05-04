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

# === Session state ===
user_state = {}
SESSION_TTL = 10  # seconds

# === Matching Logic ===
def match_entries(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    direct_exact = []
    family_exact = []
    suggestions = []

    for row in data:
        r_city = row.get('City', '').lower()
        if city_lower not in r_city:
            continue

        first = row.get('Registrant First Name', '')
        last = row.get('Registrant Last Name', '')
        full_name = f"{first} {last}".strip().lower()

        if full_name == name_lower:
            direct_exact.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        # Check exact family member match
        matched_line = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower() == name_lower:
                matched_line = line.strip()
                break
        if matched_line:
            family_exact.append({'row': row, 'via_family': True, 'matched_family': matched_line})
            continue

        # Add close suggestions (fuzzy logic, not used if exact matches exist)
        if fuzz.token_set_ratio(name_lower, full_name) >= 85:
            suggestions.append({'row': row, 'via_family': False, 'matched_family': None})
        else:
            for line in row.get('Additional Family Members', '').split('\n'):
                if fuzz.token_set_ratio(name_lower, line.lower()) >= 85:
                    suggestions.append({'row': row, 'via_family': True, 'matched_family': line.strip()})
                    break

    if direct_exact:
        return direct_exact
    if family_exact:
        return family_exact
    return suggestions

# === Format Result ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None')
    base = f"""✔️ *{full_name}* is registered.
👥 Attendees: {attendees}
👨‍👩‍👧‍👦 Family:
{family.strip() if family else 'None'}"""
    if entry['via_family']:
        base += f"\n\n*Matched via family member:* _{entry['matched_family']}_"
    return base

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use `b FullName City` to check registration.\nExample: `b Sharad Patel Irving`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if not text.lower().startswith("b "):
        return

    text = text[2:].strip()

    # Session expiration
    state = user_state.get(chat_id, {})
    if 'timestamp' in state and now - state['timestamp'] > SESSION_TTL:
        del user_state[chat_id]
        state = {}

    if 'awaiting_choice' in state:
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
            await update.message.reply_text("❗ Invalid input. Send number or new query like `b Kunj Addison`")
            return

    tokens = text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Format: `b LastName City` or `b FullName City`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = match_entries(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No registrations found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, m in enumerate(matches, 1):
            r = m['row']
            full = f"{r.get('Registrant First Name', '')} {r.get('Registrant Last Name', '')}"
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"{i}. {full} – {r.get('Attendees') or '?'} attendees{note}\n"
        reply += "\nPlease reply with the number to view details."
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
                    "⏳ Waited 10 seconds... no reply received.\n🤖 I'm moving on. Send a new query like `b Patel Frisco`!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === Init Bot ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

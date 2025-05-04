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

user_state = {}  # chat_id -> dict(state, matches, timestamp)
SESSION_TTL = 120  # seconds

def fuzzy_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    results = []

    for row in data:
        # Match on main name
        r_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".lower()
        r_city = row.get('City', '').lower()
        if fuzz.partial_ratio(name_lower, r_name) >= 75 and city_lower in r_city:
            results.append({
                'row': row,
                'via_family': False,
                'matched_family': None
            })
            continue

        # Match on family member names
        family = row.get('Additional Family Members', '')
        matched_family = None
        for line in family.split('\n'):
            if fuzz.partial_ratio(name_lower, line.lower()) >= 75 and city_lower in r_city:
                matched_family = line.strip()
                break

        if matched_family:
            results.append({
                'row': row,
                'via_family': True,
                'matched_family': matched_family
            })

    return results

def format_entry(result):
    row = result['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None').strip()

    match_info = ""
    if result['via_family']:
        match_info = f"👫 *Matched via family member:* {result['matched_family']}\n"

    return f"""✅ *{full_name}* is registered.
{match_info}👥 *Attendees:* {attendees}
👨‍👩‍👧‍👦 *Family:*\n{family if family else 'None'}"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! To check registration, type something like:\n`b Patel Frisco` or `b Kunj Addison`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if chat_id in user_state:
        state = user_state[chat_id]
        if 'timestamp' in state and now - state['timestamp'] > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    # Awaiting number?
    if 'awaiting_choice' in state:
        if text.strip().isdigit():
            idx = int(text.strip()) - 1
            if 0 <= idx < len(state['matches']):
                selected = state['matches'][idx]
                await update.message.reply_text(format_entry(selected), parse_mode='Markdown')
                del user_state[chat_id]
                return
            else:
                await update.message.reply_text("❗ Invalid number. Please try again.")
                return
        elif len(text.strip().split()) >= 2 and text.lower().startswith("b "):
            del user_state[chat_id]  # treat as new query
        else:
            await update.message.reply_text("❗ Invalid choice. Please enter a number or a new name+city starting with `b`.")
            return

    # Only respond to "b " trigger
    if not text.lower().startswith("b "):
        return

    tokens = text[2:].strip().split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Format should be like `b LastName City` or `b FullName City`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No matches found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, result in enumerate(matches, 1):
            row = result['row']
            full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
            if result['via_family']:
                reply += f"{i}. *{full_name}* (matched via family: {result['matched_family']}) – {attendees} attendees\n"
            else:
                reply += f"{i}. *{full_name}* – {attendees} attendees\n"
        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_warning():
            await asyncio.sleep(10)
            current_state = user_state.get(chat_id, {})
            if 'awaiting_choice' in current_state and current_state['timestamp'] == now:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ Waited 10 seconds... no reply received.\n🤖 I'm moving on. Send a name and city again starting with `b` if you'd like to retry!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

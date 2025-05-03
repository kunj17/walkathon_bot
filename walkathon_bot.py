# walkathon_bot.py
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
user_state = {}  # chat_id -> dict(state, matches, timestamp, expired)
SESSION_TTL = 10  # seconds

def fuzzy_match(name, city, data):
    results = []
    name_lower = name.lower()
    city_lower = city.lower()

    for row in data:
        first = row.get('Registrant First Name', '')
        last = row.get('Registrant Last Name', '')
        r_name = f"{first} {last}".lower()
        r_city = row.get('City', '').lower()

        match_main = (
            fuzz.partial_ratio(name_lower, r_name) >= 80 or
            fuzz.partial_ratio(name_lower, last.lower()) >= 90
        )

        matched_family_name = None
        family_field = row.get('Additional Family Members', '')
        for part in family_field.split(","):
            if fuzz.partial_ratio(name_lower, part.strip().lower()) >= 80:
                matched_family_name = part.strip()
                break

        if (match_main or matched_family_name) and city_lower in r_city:
            results.append({
                "row": row,
                "matched_family": matched_family_name
            })

    return results

def format_entry(row, matched_family=None):
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None')

    extra_note = f" (matched via family: {matched_family})" if matched_family else ""
    return f"""✔️ *{full_name}* is registered.{extra_note}
👥 Attendees: {attendees}
👨‍👩‍👧‍👦 Family:
{family.strip() if family else 'None'}
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Use `b name city` to search. For example:\n`b Patel Frisco` or `b Kunj Addison`"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if chat_id in user_state:
        state = user_state[chat_id]
        if state.get("expired") or now - state['timestamp'] > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    if 'awaiting_choice' in state:
        if text.strip().isdigit():
            idx = int(text.strip()) - 1
            if 0 <= idx < len(state['matches']):
                match = state['matches'][idx]
                response = format_entry(match['row'], match.get('matched_family'))
                await update.message.reply_text(response, parse_mode='Markdown')
                del user_state[chat_id]
                return
            else:
                await update.message.reply_text("❗ Invalid number. Please try again.")
                return
        elif text.lower().startswith("b ") and len(text.strip().split()) >= 3:
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Invalid choice. Please enter a number or new query with `b name city`.")
            return

    # Only respond to queries starting with 'b '
    if not text.lower().startswith("b "):
        return

    tokens = text[2:].strip().split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Please send in the format: `b LastName City` or `b FullName City`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No matches found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    if len(matches) == 1:
        m = matches[0]
        await update.message.reply_text(format_entry(m['row'], m.get('matched_family')), parse_mode='Markdown')
    else:
        reply = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, m in enumerate(matches, 1):
            row = m['row']
            name_str = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            if m.get("matched_family"):
                name_str += f" (matched via family: {m['matched_family']})"
            reply += f"{i}. {name_str} – {row.get('Attendees') or row.get('Atten Additional Family Members', '?')} attendees\n"

        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now,
            'expired': False
        }

        async def timeout_warning():
            await asyncio.sleep(SESSION_TTL)
            current_state = user_state.get(chat_id, {})
            if current_state.get('awaiting_choice') and current_state['timestamp'] == now:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ Waited 10 seconds... no reply received.\n🤖 I'm moving on. If you'd like to try again, just send a name and city starting with `b`!"
                )
                current_state["expired"] = True

        asyncio.create_task(timeout_warning())

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

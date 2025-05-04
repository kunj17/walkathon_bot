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
SESSION_TTL = 120  # seconds

def fuzzy_match_all(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    results = []

    for row in data:
        registrant_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".lower()
        reg_city = row.get('City', '').lower()

        match_score = fuzz.partial_ratio(name_lower, registrant_name)
        city_match = city_lower in reg_city

        if match_score >= 80 and city_match:
            results.append({
                "row": row,
                "match_type": "registrant",
                "matched_name": None
            })
            continue

        # Search inside family members
        family_str = row.get("Additional Family Members", "")
        matched_family = None
        for entry in family_str.split('\n'):
            if fuzz.partial_ratio(name_lower, entry.lower()) >= 80:
                matched_family = entry.strip()
                break

        if matched_family and city_match:
            results.append({
                "row": row,
                "match_type": "family",
                "matched_name": matched_family
            })

    return results

def format_entry(result):
    row = result["row"]
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None')
    match_note = ""

    if result["match_type"] == "family":
        match_note = f"🧑‍🤝‍🧑 *Matched via family member:* `{result['matched_name']}`\n"

    formatted_family = "\n".join([
        f"{'👉 ' if f.strip() == result['matched_name'] else ''}{f.strip()}"
        for f in family.strip().split('\n') if f.strip()
    ]) or "None"

    return (
        f"✅ *{full_name}* is registered.\n"
        f"{match_note}"
        f"👥 *Attendees:* {attendees}\n"
        f"👨‍👩‍👧‍👦 *Family:*\n{formatted_family}"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send a message like `b Patel Frisco` or `b Manojbhai Wylie` to check registration."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    if not text.lower().startswith("b "):
        return

    query = text[2:].strip()
    if chat_id in user_state:
        state = user_state[chat_id]
        if now - state.get("timestamp", 0) > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

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
        elif len(query.split()) >= 2:
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Invalid choice. Send a valid number or a new name+city query.")
            return

    tokens = query.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Format must be: `b LastName City` or `b FullName City`")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match_all(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No matches found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    if len(matches) == 1:
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, result in enumerate(matches, 1):
            row = result["row"]
            n = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
            suffix = f"(matched via family: {result['matched_name']})" if result["match_type"] == "family" else ""
            reply += f"{i}. *{n}* {suffix} – {attendees} attendees\n"
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

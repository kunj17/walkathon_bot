import os
import json
import time
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    CommandHandler, MessageHandler, filters
)
from fuzzywuzzy import fuzz
from decrypt_utils import decrypt_file
import asyncio

# === Load env + decrypt data ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

# === Decrypt and load data ===
decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Session cache with TTL ===
user_state = {}  # chat_id -> dict(state, matches, timestamp)
SESSION_TTL = 10  # seconds timeout for number reply

# === Helper Functions ===
def fuzzy_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    results = []

    for row in data:
        full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".lower()
        last_name = row.get('Registrant Last Name', '').lower()
        r_city = row.get('City', '').lower()

        # Primary registrant match
        if (fuzz.partial_ratio(name_lower, full_name) >= 80 or fuzz.partial_ratio(name_lower, last_name) >= 90) and city_lower in r_city:
            results.append((row, None))
            continue

        # Family member fuzzy search
        family_info = row.get('Additional Family Members', '')
        if family_info:
            for line in family_info.split("\n"):
                if fuzz.partial_ratio(name_lower, line.lower()) >= 85 and city_lower in r_city:
                    results.append((row, line.strip()))
                    break

    return results

def format_entry(row, matched_family=None):
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None')

    match_note = f"\n👫 *Matched via family member:* {matched_family}" if matched_family else ""

    # Highlight matched family member
    if matched_family and family:
        family_lines = family.strip().split("\n")
        formatted_family = "\n".join([
            f"*{line}*" if matched_family.lower() in line.lower() else line
            for line in family_lines
        ])
    else:
        formatted_family = family.strip() if family else "None"

    return f"""✅ *{full_name}* is registered.{match_note}
👥 *Attendees:* {attendees}
👨‍👩‍👧‍👦 *Family:*\n{formatted_family}"""

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send `b FullName City` or `b LastName City` to check registration."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    now = time.time()

    # Only respond if message starts with b or B
    if not text.lower().startswith("b "):
        return

    command = text[2:].strip()

    # Session cleanup
    if chat_id in user_state:
        state = user_state[chat_id]
        if 'timestamp' in state and now - state['timestamp'] > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    if 'awaiting_choice' in state:
        if command.strip().isdigit():
            idx = int(command.strip()) - 1
            if 0 <= idx < len(state['matches']):
                row, family_match = state['matches'][idx]
                response = format_entry(row, family_match)
                await update.message.reply_text(response, parse_mode='Markdown')
                del user_state[chat_id]
                return
            else:
                await update.message.reply_text("❗ Invalid number. Please try again.")
                return
        elif len(command.strip().split()) >= 2:
            del user_state[chat_id]  # treat as new query
        else:
            await update.message.reply_text("❗ Invalid choice. Send a number or a new name+city query.")
            return

    tokens = command.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Use format: `b LastName City` or `b FullName City`.")
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No matches found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    if len(matches) == 1:
        row, matched_family = matches[0]
        await update.message.reply_text(format_entry(row, matched_family), parse_mode='Markdown')
    else:
        reply = f"Found *{len(matches)}* possible registrations:\n\n"
        for i, (row, matched_family) in enumerate(matches, 1):
            n = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
            if matched_family:
                reply += f"{i}. {n} (matched via family: {matched_family}) – {attendees} attendees\n"
            else:
                reply += f"{i}. {n} – {attendees} attendees\n"

        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

        async def timeout_warning():
            await asyncio.sleep(SESSION_TTL)
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

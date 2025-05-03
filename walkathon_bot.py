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

# === Helper Functions ===
def fuzzy_match(name, city, data):
    results = []
    name_lower = name.lower()
    city_lower = city.lower()
    for row in data:
        r_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}".lower()
        r_city = row.get('City', '').lower()
        if fuzz.partial_ratio(name_lower, r_name) >= 80 and city_lower in r_city:
            results.append(row)
        elif fuzz.partial_ratio(name_lower, row.get('Registrant Last Name', '').lower()) >= 90 and city_lower in r_city:
            results.append(row)
    return results

def format_entry(row):
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    phone = row.get('Phone', 'N/A')
    family = row.get('Additional Family Members', 'None')
    return f"""✔️ *{full_name}* is registered.
👥 Attendees: {attendees}
📞 Phone: {phone}
👨‍👩‍👧‍👦 Family:
{family.strip() if family else 'None'}
"""

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Please send a message like `Patel Frisco` or `Manojbhai Wylie` to check registration."
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

    if 'awaiting_choice' in state:
        try:
            idx = int(text.strip()) - 1
            selected = state['matches'][idx]
            response = format_entry(selected)
            await update.message.reply_text(response, parse_mode='Markdown')
            del user_state[chat_id]
        except:
            await update.message.reply_text("❗ Invalid choice. Please send a valid number.")
        return

    # New name+city query
    tokens = text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Please send in the format: `LastName City` or `FullName City`")
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
        for i, row in enumerate(matches, 1):
            n = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            reply += f"{i}. {n} – {row.get('Attendees') or row.get('Atten Additional Family Members', '?')} attendees\n"
        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')
        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches,
            'timestamp': now
        }

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

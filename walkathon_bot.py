import os
import json
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, ContextTypes,
    MessageHandler, filters
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

# === Session cache ===
user_state = {}  # chat_id -> dict(state, matches, etc)

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
    attendees = row.get('Attendees', '?')
    phone = row.get('Phone', 'N/A')
    family = row.get('Additional Family Members', 'None')
    return f"""✔️ *{full_name}* is registered.
👥 Attendees: {attendees}
📞 Phone: {phone}
👨‍👩‍👧‍👦 Family:
{family.strip() if family else 'None'}
"""

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if chat_id not in user_state:
        user_state[chat_id] = {}

    state = user_state[chat_id]

    if 'awaiting_choice' in state:
        try:
            idx = int(text.strip()) - 1
            selected = state['matches'][idx]
            response = format_entry(selected)
            await update.message.reply_text(response, parse_mode='Markdown')
            del user_state[chat_id]
        except:
            await update.message.reply_text("Invalid choice. Please send a valid number.")
        return

    # Process new query
    tokens = text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Please send 'LastName City' or 'Full Name City'")
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
            reply += f"{i}. {n} – {row.get('Attendees', '?')} attendees\n"
        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')
        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': matches
        }

# === App Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()


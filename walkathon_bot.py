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

# Load and decrypt JSON
decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Session state ===
user_state = {}  # chat_id -> dict(state, matches, timestamp)
SESSION_TTL = 10
TRIGGER_PREFIX = "b "

# === Matching ===
def fuzzy_match(name, city, data):
    results = []
    name_lower = name.lower()
    city_lower = city.lower()
    for row in data:
        first = row.get("Registrant First Name", "")
        last = row.get("Registrant Last Name", "")
        full_name = f"{first} {last}".lower()
        row_city = row.get("City", "").lower()

        # Direct registrant match
        if fuzz.partial_ratio(name_lower, full_name) >= 80 and city_lower in row_city:
            results.append((row, None))
            continue

        # Family member match
        family = row.get("Additional Family Members", "")
        matched = []
        for line in family.split("\n"):
            if fuzz.partial_ratio(name_lower, line.lower()) >= 80:
                matched.append(line.strip())
        if matched and city_lower in row_city:
            results.append((row, matched))
    return results

# === Formatter ===
def format_entry(row, matched_family=None):
    name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees') or row.get('Atten Additional Family Members', '?')
    family = row.get('Additional Family Members', 'None')
    family_lines = family.strip().split("\n") if family else []

    lines = [f"✔️ *{name}* is registered."]
    if matched_family:
        lines.append(f"🧑‍🤝‍🧑 *Matched via family member:* *{matched_family[0]}*")
    lines.append(f"👥 *Attendees:* {attendees}")
    if family_lines:
        lines.append("\U0001F468‍\U0001F469‍\U0001F467 *Family:*")
        for f in family_lines:
            lines.append(f"{('*' if matched_family and f in matched_family else '')}{f}{('*' if matched_family and f in matched_family else '')}")
    return "\n".join(lines)

# === Bot handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Send a message like `b Patel Frisco` to check registration.")

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

    # --- Only allow trigger-based commands ---
    if not text.lower().startswith(TRIGGER_PREFIX):
        if 'awaiting_choice' in state and text.strip().isdigit():
            idx = int(text.strip()) - 1
            if 0 <= idx < len(state['matches']):
                row, matched_family = state['matches'][idx]
                response = format_entry(row, matched_family)
                await update.message.reply_text(response, parse_mode='Markdown')
                del user_state[chat_id]
                return
            else:
                await update.message.reply_text("❗ Invalid number. Please try again.")
                return
        return

    # --- Triggered Search ---
    clean_text = text[len(TRIGGER_PREFIX):].strip()
    tokens = clean_text.split()
    if len(tokens) < 2:
        await update.message.reply_text("❗ Use: `b LastName City` or `b FirstName LastName City`", parse_mode='Markdown')
        return

    name = " ".join(tokens[:-1])
    city = tokens[-1]
    matches = fuzzy_match(name, city, registration_data)

    if not matches:
        await update.message.reply_text(f"No matches found for *{name}* in *{city}*", parse_mode='Markdown')
        return

    # Filter to family-only matches if not direct
    family_only = [m for m in matches if m[1] is not None]
    use_matches = matches if len(matches) == 1 else family_only if family_only else matches

    if len(use_matches) == 1:
        row, fam = use_matches[0]
        await update.message.reply_text(format_entry(row, fam), parse_mode='Markdown')
    else:
        reply = f"Found *{len(use_matches)}* possible registrations:\n\n"
        for i, (row, fam) in enumerate(use_matches, 1):
            n = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            if fam:
                fam_str = f" (matched via family: {', '.join(fam)})"
            else:
                fam_str = ""
            reply += f"{i}. {n}{fam_str} – {row.get('Attendees', '?')} attendees\n"
        reply += "\nPlease reply with the number to view details."
        await update.message.reply_text(reply, parse_mode='Markdown')

        user_state[chat_id] = {
            'awaiting_choice': True,
            'matches': use_matches,
            'timestamp': now
        }

        async def timeout():
            await asyncio.sleep(SESSION_TTL)
            curr = user_state.get(chat_id)
            if curr and curr.get('timestamp') == now:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⏳ Waited 10 seconds... no reply received.\n🤖 I'm moving on. If you'd like to try again, just send a name and city starting with `b` !"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout())

# === Run ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

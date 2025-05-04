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
from decrypt_utils import decrypt_file

# === Load env + decrypt data ===
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GPG_PASSPHRASE = os.getenv("GPG_PASSPHRASE")

decrypted_path = decrypt_file(GPG_PASSPHRASE)
with open(decrypted_path, 'r') as f:
    registration_data = json.load(f)

# === Session Cache ===
user_state = {}  # chat_id -> dict(state)
SESSION_TTL = 15  # seconds

# === Matching Logic ===
def prefix_match(name, city, data):
    name_lower = name.lower()
    city_lower = city.lower()
    direct = []
    family = []

    for row in data:
        r_city = row.get('City', '').lower()
        if city_lower and not r_city.startswith(city_lower):
            continue

        r_fname = row.get('Registrant First Name', '').lower()
        r_lname = row.get('Registrant Last Name', '').lower()
        full_name = f"{r_fname} {r_lname}"

        if r_fname.startswith(name_lower) or r_lname.startswith(name_lower) or full_name.startswith(name_lower):
            direct.append({'row': row, 'via_family': False, 'matched_family': None})
            continue

        matched_line = None
        for line in row.get('Additional Family Members', '').split('\n'):
            if line.strip().lower().startswith(name_lower):
                matched_line = line.strip()
                break
        if matched_line:
            family.append({'row': row, 'via_family': True, 'matched_family': matched_line})

    return direct if direct else family

# === Format Entry ===
def format_entry(entry):
    row = entry['row']
    full_name = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
    attendees = row.get('Attendees', '?')
    family = row.get('Additional Family Members', 'None').strip()

    response = f"""✅ *{full_name}* is registered.
👥 *Attendees:* {attendees}
👨‍👩‍👧 *Family Members:*
{family if family else 'None'}"""

    if entry['via_family']:
        response += f"\n🧑‍🤝‍🧑 *Matched via family member:* *{entry['matched_family']}*"
    return response

# === Handlers ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome! Send:\n"
        "`b FirstName City`\n"
        "`b LastName City`\n"
        "`b FirstName LastName City`\n"
        "`b FirstName`\n"
        "`b LastName`\n"
        "Supports flexible matching & partial names. Try: `b hem mck`, `b sharad`, `b ranjan patel irving`",
        parse_mode='Markdown'
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.replace("\n", " ").strip()
    chat_id = update.effective_chat.id
    now = time.time()

    print(f"[{time.strftime('%X')}] Message from {chat_id}: {text}")

    if chat_id in user_state:
        state = user_state[chat_id]
        if now - state.get('timestamp', 0) > SESSION_TTL:
            del user_state[chat_id]
            state = {}
    else:
        state = {}

    # === Handle Numeric Replies ===
    if 'awaiting_choice' in state and text.isdigit():
        idx = int(text) - 1
        matches = state.get('matches', [])
        if 0 <= idx < len(matches):
            print(f"[{chat_id}] Selected match #{idx + 1}: {matches[idx]['row'].get('Registrant Last Name')}")
            await update.message.reply_text(format_entry(matches[idx]), parse_mode='Markdown')
            del user_state[chat_id]
        else:
            await update.message.reply_text("❗ Invalid number.")
        return

    # === Only process trigger "b "
    if not text.lower().startswith("b "):
        return

    text = text[2:].strip()
    tokens = text.split()

    name = " ".join(tokens[:-1]) if len(tokens) >= 2 else tokens[0]
    city = tokens[-1] if len(tokens) >= 2 else ""

    print(f"[{chat_id}] Searching for: name='{name}', city='{city}'")

    matches = prefix_match(name, city, registration_data)

    if not matches:
        print(f"[{chat_id}] No match found.")
        await update.message.reply_text(
            f"❌ No matches found for *{name}*{f' in *{city}*' if city else ''}.\n"
            "🔍 Try another spelling or family member.",
            parse_mode='Markdown'
        )
        return

    if len(matches) == 1:
        print(f"[{chat_id}] One match found: {matches[0]['row'].get('Registrant Last Name')}")
        await update.message.reply_text(format_entry(matches[0]), parse_mode='Markdown')
    else:
        reply = f"🔎 *Found {len(matches)} possible matches:*\n\n"
        for i, m in enumerate(matches, 1):
            row = m['row']
            full = f"{row.get('Registrant First Name', '')} {row.get('Registrant Last Name', '')}"
            attendees = row.get('Attendees', '?')
            note = f" _(via family: {m['matched_family']})_" if m['via_family'] else ""
            reply += f"*{i}. {full}* — {attendees} attendees{note}\n"
        reply += "\n✉️ *Reply with the number to view full details.*"

        print(f"[{chat_id}] Multiple matches found.")
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
                print(f"[{chat_id}] Session expired after timeout.")
                await context.bot.send_message(
                    chat_id,
                    "⏳ Waited 15 seconds… no reply received.\nSend a new query like `b Patel Frisco`!"
                )
                del user_state[chat_id]

        asyncio.create_task(timeout_warning())

# === Bot Init ===
app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
app.run_polling()

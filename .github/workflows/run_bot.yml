name: Run Walkathon Bot (Persistent)

on:
  workflow_dispatch:

jobs:
  run-telegram-bot:
    runs-on: ubuntu-latest
    timeout-minutes: 4320  # 3 days max run time
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.10"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Start Bot
        env:
          TELEGRAM_TOKEN: ${{ secrets.TELEGRAM_TOKEN }}
          GPG_PASSPHRASE: ${{ secrets.GPG_PASSPHRASE }}
        run: |
          echo "🔁 Starting Walkathon Bot"
          python walkathon_bot.py

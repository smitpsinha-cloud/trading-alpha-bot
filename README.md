
# Trading Alpha Bot — Live deployment

## 1. Local test
PowerShell:
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt

Set environment variables:
$env:BOT_TOKEN="NEW_BOT_TOKEN"
$env:PROGRAM_UPI_ID="YOUR-UPI@BANK"
$env:ADMIN_IDS="YOUR_TELEGRAM_ID"

Run:
python bot.py

## 2. Production
Recommended free starting host: Oracle Cloud Free Tier VM.
Keep the bot process under systemd so it restarts automatically.

On Ubuntu:
sudo apt update
sudo apt install -y python3-venv git

git clone YOUR_REPOSITORY_URL
cd trading_alpha_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

Create an environment file outside Git:
sudo nano /etc/trading-alpha-bot.env

BOT_TOKEN=YOUR_NEW_TOKEN
PROGRAM_UPI_ID=YOUR-UPI@BANK
ADMIN_IDS=123456789

Then:
sudo chmod 600 /etc/trading-alpha-bot.env

Create service:
sudo nano /etc/systemd/system/trading-alpha-bot.service

[Unit]
Description=Trading Alpha Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/trading_alpha_bot
EnvironmentFile=/etc/trading-alpha-bot.env
ExecStart=/home/ubuntu/trading_alpha_bot/venv/bin/python /home/ubuntu/trading_alpha_bot/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target

Then:
sudo systemctl daemon-reload
sudo systemctl enable --now trading-alpha-bot
sudo systemctl status trading-alpha-bot

Logs:
journalctl -u trading-alpha-bot -f

## Important
Do NOT commit BOT_TOKEN, UPI credentials, screenshots, bot.db, or secrets to GitHub.
Add bot.db to .gitignore.
For a large production deployment, migrate from SQLite to PostgreSQL and store screenshots in object storage.

# Trading Alpha Bot — Railway + PostgreSQL

## Railway variables
Set these on the bot service:
- BOT_TOKEN
- PROGRAM_UPI_ID
- ADMIN_IDS
- DATABASE_URL = `${{Postgres.DATABASE_URL}}`

Railway provides DATABASE_URL from the PostgreSQL service. Do not commit real secrets.

## Deploy
Push these files to the GitHub repository connected to Railway. Railway will build the Python service and use `python bot.py` as the start command.

## First test
1. Use a test Telegram account.
2. `/start`
3. Submit a test payment record; do not use real funds until the ledger and admin workflow are verified.
4. Verify/reject from the admin account.
5. Check `/payouts` and `/settlements`.

## Notes
- Daily payout queue is generated at 10:00 Asia/Kolkata.
- Payout is calculated as 10% of verified active amount.
- Active amount is capped at ₹10,000.
- This package does not send messages to arbitrary Telegram users who have not started/interacted with the bot.
- For large production use, consider a separate worker/scheduler and stronger operational controls.

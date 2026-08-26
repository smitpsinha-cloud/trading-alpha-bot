import os
import datetime as dt
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application,CommandHandler,CallbackQueryHandler,ConversationHandler,MessageHandler,ContextTypes,filters
import database

BOT_TOKEN=os.environ["BOT_TOKEN"]
PROGRAM_UPI_ID=os.environ["PROGRAM_UPI_ID"]
ADMIN_IDS={int(x.strip()) for x in os.environ.get("ADMIN_IDS","").split(",") if x.strip()}
MIN_ADD=1000
MAX_ACTIVE=10000
RATE=10
IST=ZoneInfo("Asia/Kolkata")

AMOUNT,USER_UPI,UTR,SCREENSHOT=range(4)
PAYOUT_UTR,PAYOUT_SCREENSHOT=range(10,12)
SETTLE_UTR,SETTLE_SCREENSHOT=range(20,22)

def money(n): return f"₹{int(n):,}"
def today(): return datetime.now(IST).date().isoformat()
def daily_payout(amount): return amount*RATE//100

def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 My Account",callback_data="account"),
         InlineKeyboardButton("➕ Add / Increase Amount",callback_data="add_amount")],
        [InlineKeyboardButton("💳 Payment Status",callback_data="payment_status"),
         InlineKeyboardButton("❌ Opt Out",callback_data="opt_out")]
    ])

async def start(update,context):
    u=update.effective_user
    database.register_user(u.id,u.username,u.first_name,u.last_name)
    await update.message.reply_text("Namaskar! 👋\n\nWelcome. Please choose an option:",reply_markup=main_menu())

async def account(update,context):
    q=update.callback_query; await q.answer()
    u=database.get_user(q.from_user.id)
    if not u: await q.edit_message_text("Please send /start first."); return
    await q.edit_message_text(
        f"💰 MY ACCOUNT\n\nStatus: {u['status']}\nActive amount: {money(u['active_amount'])}\n"
        f"Daily payout: {money(daily_payout(u['active_amount']))}\n\n"
        "Your active amount changes only after payment verification.",
        reply_markup=main_menu())

async def add_amount_start(update,context):
    q=update.callback_query; await q.answer()
    u=database.get_user(q.from_user.id)
    if not u or u["status"] not in ("ACTIVE","PENDING"):
        await q.edit_message_text("Please send /start first."); return ConversationHandler.END
    remaining=MAX_ACTIVE-u["active_amount"]
    if remaining<MIN_ADD:
        await q.edit_message_text("Your active amount has reached the ₹10,000 maximum."); return ConversationHandler.END
    await q.edit_message_text(
        f"➕ ADD / INCREASE AMOUNT\n\nCurrent active amount: {money(u['active_amount'])}\n"
        f"Maximum total: {money(MAX_ACTIVE)}\n\nEnter the additional amount (minimum {money(MIN_ADD)}).\n"
        "Example: 2500\n\n/cancel to cancel.")
    return AMOUNT

async def amount_received(update,context):
    try: amount=int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("Please enter a whole number, e.g. 2500."); return AMOUNT
    u=database.get_user(update.effective_user.id); remaining=MAX_ACTIVE-u["active_amount"]
    if amount<MIN_ADD:
        await update.message.reply_text(f"Minimum additional amount is {money(MIN_ADD)}."); return AMOUNT
    if amount>remaining:
        await update.message.reply_text(f"Maximum additional amount allowed is {money(remaining)}."); return AMOUNT
    context.user_data["amount"]=amount
    await update.message.reply_text(f"Payment amount: {money(amount)}\n\nUPI ID to pay:\n{PROGRAM_UPI_ID}\n\nAfter paying, enter the UPI ID you paid from.")
    return USER_UPI

async def user_upi_received(update,context):
    v=update.message.text.strip()
    if len(v)<3: await update.message.reply_text("Please enter a valid UPI ID."); return USER_UPI
    context.user_data["upi"]=v
    await update.message.reply_text("Now enter the UTR / transaction ID.")
    return UTR

async def utr_received(update,context):
    v=update.message.text.strip()
    if len(v)<4: await update.message.reply_text("Please enter a valid UTR / transaction ID."); return UTR
    context.user_data["utr"]=v
    await update.message.reply_text("Please upload the payment screenshot as a photo.")
    return SCREENSHOT

async def screenshot_received(update,context):
    if not update.message.photo:
        await update.message.reply_text("Please upload the screenshot as a photo."); return SCREENSHOT
    tid=update.effective_user.id; amount=context.user_data["amount"]
    upi=context.user_data["upi"]; utr=context.user_data["utr"]; fid=update.message.photo[-1].file_id
    did=database.create_deposit(tid,amount,upi,utr,fid)
    await update.message.reply_text(f"✅ PAYMENT SUBMITTED\n\nAmount: {money(amount)}\nUTR: {utr}\n\nPending Admin verification. Your active amount will not change until verification.")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify",callback_data=f"verify:{did}"),
                              InlineKeyboardButton("❌ Reject",callback_data=f"reject:{did}")]])
    cap=f"🔔 NEW PAYMENT\n\nDeposit #{did}\nUser: @{update.effective_user.username or 'none'}\nTelegram ID: {tid}\nAmount: {money(amount)}\nUPI: {upi}\nUTR: {utr}"
    for aid in ADMIN_IDS:
        try: await context.bot.send_photo(aid,fid,caption=cap,reply_markup=kb)
        except Exception as e: print("Admin notify:",e)
    context.user_data.clear(); return ConversationHandler.END

async def cancel(update,context):
    context.user_data.clear(); await update.message.reply_text("Cancelled.",reply_markup=main_menu()); return ConversationHandler.END

REASONS={"utr":"UTR not found / could not be verified","amount":"Amount mismatch","duplicate":"Duplicate transaction","screenshot":"Screenshot unclear","other":"Other"}

async def admin_action(update,context):
    q=update.callback_query
    if q.from_user.id not in ADMIN_IDS: await q.answer("Not authorized.",show_alert=True); return
    await q.answer(); action,did=q.data.split(":"); did=int(did)
    if action=="verify":
        r=database.verify_deposit(did,q.from_user.id)
        if r=="LIMIT":
            await q.edit_message_caption(caption=(q.message.caption or "")+"\n\n❌ Cannot verify: ₹10,000 limit would be exceeded."); return
        if not r:
            await q.edit_message_caption(caption=(q.message.caption or "")+"\n\n⚠️ Already processed or not found."); return
        await q.edit_message_caption(caption=(q.message.caption or "")+"\n\n✅ VERIFIED")
        await context.bot.send_message(r["telegram_id"],f"✅ PAYMENT VERIFIED\n\nVerified amount: {money(r['amount'])}\nActive amount: {money(r['active_amount'])}\nDaily payout: {money(daily_payout(r['active_amount']))}")
    else:
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("UTR not found",callback_data=f"reason:{did}:utr"),InlineKeyboardButton("Amount mismatch",callback_data=f"reason:{did}:amount")],
            [InlineKeyboardButton("Duplicate",callback_data=f"reason:{did}:duplicate"),InlineKeyboardButton("Screenshot unclear",callback_data=f"reason:{did}:screenshot")],
            [InlineKeyboardButton("Other",callback_data=f"reason:{did}:other")]])
        await q.edit_message_reply_markup(reply_markup=kb)

async def reject_reason(update,context):
    q=update.callback_query
    if q.from_user.id not in ADMIN_IDS: await q.answer("Not authorized.",show_alert=True); return
    await q.answer(); _,did,reason=q.data.split(":")
    d=database.reject_deposit(int(did),q.from_user.id,REASONS[reason])
    if not d: await q.edit_message_caption(caption=(q.message.caption or "")+"\n\n⚠️ Already processed or not found."); return
    await q.edit_message_caption(caption=(q.message.caption or "")+f"\n\n❌ REJECTED: {REASONS[reason]}")
    await context.bot.send_message(d["telegram_id"],f"❌ PAYMENT REJECTED\n\nAmount: {money(d['amount'])}\nReason: {REASONS[reason]}\n\nYour active amount was not increased.")

async def payment_status(update,context):
    q=update.callback_query; await q.answer()
    mine=[r for r in database.pending_deposits() if r["telegram_id"]==q.from_user.id]
    text="💳 PAYMENT STATUS\n\n"+("\n".join(f"Deposit #{r['id']}: {money(r['amount'])} — Pending" for r in mine) if mine else "No pending payment was found.")
    await q.edit_message_text(text,reply_markup=main_menu())

async def opt_out(update,context):
    q=update.callback_query; await q.answer()
    u=database.get_user(q.from_user.id)
    if not u or u["status"] not in ("ACTIVE","OPT_OUT_PENDING") or u["active_amount"]<=0:
        await q.edit_message_text("There is no active amount available for opt-out.",reply_markup=main_menu()); return
    if u["status"]=="OPT_OUT_PENDING":
        await q.edit_message_text("Your opt-out settlement is already pending Admin payment."); return
    p=database.get_today_payout_for_user(q.from_user.id,today())
    if p and p["status"]=="PAID": status="PAID"; today_amt=p["payout_amount"]
    else: status="UNPAID"; today_amt=p["payout_amount"] if p else daily_payout(u["active_amount"])
    sid,final,_=database.create_settlement(q.from_user.id,status,today_amt)
    explanation=(f"Today's payout: {money(today_amt)} — already paid.\nFinal settlement: {money(final)}" if status=="PAID"
                 else f"Today's payout: {money(today_amt)} — not yet paid.\nFinal settlement: {money(final)}")
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("⚠️ Confirm Opt Out",callback_data=f"confirm_settle:{sid}"),
                              InlineKeyboardButton("Cancel",callback_data="cancel_optout")]])
    await q.edit_message_text("🚪 OPT-OUT SETTLEMENT\n\nOriginal active amount: "+money(u["active_amount"])+"\n"+explanation+"\n\nPlease confirm.",reply_markup=kb)

async def confirm_settle(update,context):
    q=update.callback_query; await q.answer()
    sid=int(q.data.split(":")[1]); s=database.get_settlement(sid)
    if not s or s["telegram_id"]!=q.from_user.id:
        await q.edit_message_text("Settlement not found."); return
    await q.edit_message_text(f"Settlement #{sid}\n\nFinal amount: {money(s['final_amount'])}\n\nAdmin has been notified. Your account remains pending settlement until payment is confirmed.")
    for aid in ADMIN_IDS:
        await context.bot.send_message(aid,f"🔴 OPT-OUT SETTLEMENT #{sid}\nUser: @{s['username'] or 'none'}\nOriginal: {money(s['original_amount'])}\nToday's payout status: {s['todays_payout_status']}\nToday's payout: {money(s['todays_payout'])}\nFINAL: {money(s['final_amount'])}")

async def cancel_optout(update,context):
    q=update.callback_query; await q.answer(); await q.edit_message_text("Opt-out cancelled.",reply_markup=main_menu())

async def admin_payouts(update,context):
    if update.effective_user.id not in ADMIN_IDS: return
    rows=database.pending_payouts(today())
    if not rows: await update.message.reply_text("No pending payouts for today."); return
    await update.message.reply_text(f"Today's pending payouts: {len(rows)}\nTotal: {money(sum(r['payout_amount'] for r in rows))}")
    for r in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Record Payment",callback_data=f"payout:{r['id']}")]])
        await update.message.reply_text(f"Payout #{r['id']}\nUser: @{r['username'] or 'none'}\nAmount: {money(r['payout_amount'])}",reply_markup=kb)

async def payout_start(update,context):
    q=update.callback_query
    if q.from_user.id not in ADMIN_IDS: await q.answer("Not authorized.",show_alert=True); return ConversationHandler.END
    await q.answer(); context.user_data["payout_id"]=int(q.data.split(":")[1]); await q.message.reply_text("Enter payout UTR / transaction ID."); return PAYOUT_UTR

async def payout_utr(update,context):
    context.user_data["payout_utr"]=update.message.text.strip(); await update.message.reply_text("Upload the payout screenshot."); return PAYOUT_SCREENSHOT

async def payout_screenshot(update,context):
    if not update.message.photo: await update.message.reply_text("Upload the screenshot as a photo."); return PAYOUT_SCREENSHOT
    r=database.mark_payout_paid(context.user_data["payout_id"],update.effective_user.id,context.user_data["payout_utr"],update.message.photo[-1].file_id)
    if not r: await update.message.reply_text("Payout already processed or not found."); return ConversationHandler.END
    await update.message.reply_text("✅ Payout recorded.")
    await context.bot.send_photo(r["telegram_id"],update.message.photo[-1].file_id,caption=f"✅ DAILY PAYOUT COMPLETED\nDate: {r['payout_date']}\nAmount: {money(r['payout_amount'])}\nUTR: {context.user_data['payout_utr']}")
    context.user_data.clear(); return ConversationHandler.END

async def admin_settlements(update,context):
    if update.effective_user.id not in ADMIN_IDS: return
    rows=database.pending_settlements()
    if not rows: await update.message.reply_text("No pending settlements."); return
    for r in rows:
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Pay Settlement",callback_data=f"settlepay:{r['id']}")]])
        await update.message.reply_text(f"Settlement #{r['id']}\nUser: @{r['username'] or 'none'}\nFinal: {money(r['final_amount'])}",reply_markup=kb)

async def settlement_start(update,context):
    q=update.callback_query
    if q.from_user.id not in ADMIN_IDS: await q.answer("Not authorized.",show_alert=True); return ConversationHandler.END
    await q.answer(); context.user_data["settlement_id"]=int(q.data.split(":")[1]); await q.message.reply_text("Enter settlement UTR."); return SETTLE_UTR

async def settlement_utr(update,context):
    context.user_data["settlement_utr"]=update.message.text.strip(); await update.message.reply_text("Upload settlement screenshot."); return SETTLE_SCREENSHOT

async def settlement_screenshot(update,context):
    if not update.message.photo: await update.message.reply_text("Upload settlement screenshot."); return SETTLE_SCREENSHOT
    r=database.mark_settlement_paid(context.user_data["settlement_id"],update.effective_user.id,context.user_data["settlement_utr"],update.message.photo[-1].file_id)
    if not r: await update.message.reply_text("Settlement already processed or not found."); return ConversationHandler.END
    await update.message.reply_text("✅ Settlement recorded.")
    await context.bot.send_photo(r["telegram_id"],update.message.photo[-1].file_id,caption=f"✅ FINAL SETTLEMENT PAID\nAmount: {money(r['final_amount'])}\nYour account is now closed.")
    context.user_data.clear(); return ConversationHandler.END

async def payout_job(context):
    rows=database.create_today_payouts(today(),RATE)
    total=sum(x[1] for x in rows)
    for aid in ADMIN_IDS:
        try: await context.bot.send_message(aid,f"⏰ DAILY PAYOUT QUEUE — 10:00 AM IST\nDate: {today()}\nUsers: {len(rows)}\nTotal due: {money(total)}\nDeadline: 1:00 PM\nUse /payouts")
        except Exception as e: print("Payout reminder:",e)

async def post_init(app):
    database.initialize_database()
    for aid in ADMIN_IDS: database.add_admin(aid,"Administrator")

async def error_handler(update,context): print("ERROR:",repr(context.error))

def main():
    if not ADMIN_IDS: raise RuntimeError("Set ADMIN_IDS.")
    app=Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    payment=ConversationHandler(
        entry_points=[CallbackQueryHandler(add_amount_start,pattern="^add_amount$")],
        states={AMOUNT:[MessageHandler(filters.TEXT&~filters.COMMAND,amount_received)],
                USER_UPI:[MessageHandler(filters.TEXT&~filters.COMMAND,user_upi_received)],
                UTR:[MessageHandler(filters.TEXT&~filters.COMMAND,utr_received)],
                SCREENSHOT:[MessageHandler(filters.PHOTO,screenshot_received)]},
        fallbacks=[CommandHandler("cancel",cancel)])
    payout_conv=ConversationHandler(
        entry_points=[CallbackQueryHandler(payout_start,pattern="^payout:")],
        states={PAYOUT_UTR:[MessageHandler(filters.TEXT&~filters.COMMAND,payout_utr)],
                PAYOUT_SCREENSHOT:[MessageHandler(filters.PHOTO,payout_screenshot)]},
        fallbacks=[CommandHandler("cancel",cancel)])
    settlement_conv=ConversationHandler(
        entry_points=[CallbackQueryHandler(settlement_start,pattern="^settlepay:")],
        states={SETTLE_UTR:[MessageHandler(filters.TEXT&~filters.COMMAND,settlement_utr)],
                SETTLE_SCREENSHOT:[MessageHandler(filters.PHOTO,settlement_screenshot)]},
        fallbacks=[CommandHandler("cancel",cancel)])
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("payouts",admin_payouts))
    app.add_handler(CommandHandler("settlements",admin_settlements))
    app.add_handler(payment); app.add_handler(payout_conv); app.add_handler(settlement_conv)
    app.add_handler(CallbackQueryHandler(admin_action,pattern="^(verify|reject):"))
    app.add_handler(CallbackQueryHandler(reject_reason,pattern="^reason:"))
    app.add_handler(CallbackQueryHandler(account,pattern="^account$"))
    app.add_handler(CallbackQueryHandler(payment_status,pattern="^payment_status$"))
    app.add_handler(CallbackQueryHandler(opt_out,pattern="^opt_out$"))
    app.add_handler(CallbackQueryHandler(confirm_settle,pattern="^confirm_settle:"))
    app.add_handler(CallbackQueryHandler(cancel_optout,pattern="^cancel_optout$"))
    app.add_error_handler(error_handler)

    now=datetime.now(IST); first=now.replace(hour=10,minute=0,second=0,microsecond=0)
    if first<=now: first+=dt.timedelta(days=1)
    app.job_queue.run_repeating(payout_job,interval=86400,first=(first-now).total_seconds())
    print("Bot running; daily payout queue scheduled for 10:00 IST.")
    app.run_polling()

if __name__=="__main__": main()

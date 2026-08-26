import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ["DATABASE_URL"]

@contextmanager
def db():
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def initialize_database():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            username TEXT, first_name TEXT, last_name TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            active_amount INTEGER NOT NULL DEFAULT 0 CHECK (active_amount >= 0 AND active_amount <= 10000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            opt_in_at TIMESTAMPTZ, opt_out_at TIMESTAMPTZ
        );
        CREATE TABLE IF NOT EXISTS deposits (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id),
            amount INTEGER NOT NULL CHECK (amount >= 1000),
            upi_id TEXT NOT NULL, utr TEXT NOT NULL,
            screenshot_file_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            rejection_reason TEXT,
            submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            verified_at TIMESTAMPTZ, verified_by BIGINT
        );
        CREATE TABLE IF NOT EXISTS daily_payouts (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id),
            payout_date DATE NOT NULL,
            eligible_amount INTEGER NOT NULL,
            payout_amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            upi_id TEXT, utr TEXT, screenshot_file_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            paid_at TIMESTAMPTZ, confirmed_by BIGINT,
            UNIQUE(user_id, payout_date)
        );
        CREATE TABLE IF NOT EXISTS settlements (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL REFERENCES users(id),
            original_amount INTEGER NOT NULL,
            todays_payout INTEGER NOT NULL DEFAULT 0,
            todays_payout_status TEXT NOT NULL,
            final_amount INTEGER NOT NULL,
            upi_id TEXT, utr TEXT, screenshot_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ, confirmed_by BIGINT
        );
        CREATE TABLE IF NOT EXISTS admins (
            telegram_id BIGINT PRIMARY KEY,
            name TEXT, active BOOLEAN NOT NULL DEFAULT TRUE
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(id),
            admin_telegram_id BIGINT,
            action TEXT NOT NULL,
            old_value TEXT, new_value TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_deposits_status ON deposits(status);
        CREATE INDEX IF NOT EXISTS idx_payouts_date_status ON daily_payouts(payout_date,status);
        CREATE INDEX IF NOT EXISTS idx_settlements_status ON settlements(status);
        """)

def register_user(tid, username, first_name, last_name):
    with db() as con:
        con.execute("""INSERT INTO users(telegram_id,username,first_name,last_name)
        VALUES(%s,%s,%s,%s)
        ON CONFLICT(telegram_id) DO UPDATE SET username=EXCLUDED.username,
        first_name=EXCLUDED.first_name,last_name=EXCLUDED.last_name""",
        (tid,username,first_name,last_name))

def get_user(tid):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE telegram_id=%s",(tid,)).fetchone()

def set_status(tid,status):
    with db() as con:
        if status=="ACTIVE":
            con.execute("UPDATE users SET status=%s,opt_in_at=NOW() WHERE telegram_id=%s",(status,tid))
        elif status=="OPTED_OUT":
            con.execute("UPDATE users SET status=%s,opt_out_at=NOW() WHERE telegram_id=%s",(status,tid))
        else:
            con.execute("UPDATE users SET status=%s WHERE telegram_id=%s",(status,tid))

def add_admin(tid,name="Admin"):
    with db() as con:
        con.execute("INSERT INTO admins(telegram_id,name) VALUES(%s,%s) ON CONFLICT DO NOTHING",(tid,name))

def create_deposit(tid,amount,upi_id,utr,screenshot):
    with db() as con:
        u=con.execute("SELECT id FROM users WHERE telegram_id=%s",(tid,)).fetchone()
        if not u: return None
        cur=con.execute("""INSERT INTO deposits(user_id,amount,upi_id,utr,screenshot_file_id)
        VALUES(%s,%s,%s,%s,%s) RETURNING id""",(u["id"],amount,upi_id,utr,screenshot))
        return cur.fetchone()["id"]

def pending_deposits():
    with db() as con:
        return con.execute("""SELECT d.*,u.telegram_id,u.username,u.first_name
        FROM deposits d JOIN users u ON u.id=d.user_id WHERE d.status='PENDING' ORDER BY d.id""").fetchall()

def verify_deposit(deposit_id,admin_id):
    with db() as con:
        d=con.execute("SELECT * FROM deposits WHERE id=%s FOR UPDATE",(deposit_id,)).fetchone()
        if not d or d["status"]!="PENDING": return None
        u=con.execute("SELECT * FROM users WHERE id=%s FOR UPDATE",(d["user_id"],)).fetchone()
        new_amount=u["active_amount"]+d["amount"]
        if new_amount>10000: return "LIMIT"
        con.execute("""UPDATE deposits SET status='VERIFIED',verified_at=NOW(),verified_by=%s WHERE id=%s""",(admin_id,deposit_id))
        con.execute("UPDATE users SET active_amount=%s,status='ACTIVE' WHERE id=%s",(new_amount,u["id"]))
        con.execute("""INSERT INTO audit_log(user_id,admin_telegram_id,action,old_value,new_value)
        VALUES(%s,%s,'DEPOSIT_VERIFIED',%s,%s)""",(u["id"],admin_id,str(u["active_amount"]),str(new_amount)))
        return {"telegram_id":u["telegram_id"],"amount":d["amount"],"active_amount":new_amount}

def reject_deposit(deposit_id,admin_id,reason):
    with db() as con:
        d=con.execute("SELECT * FROM deposits WHERE id=%s FOR UPDATE",(deposit_id,)).fetchone()
        if not d or d["status"]!="PENDING": return None
        con.execute("""UPDATE deposits SET status='REJECTED',rejection_reason=%s,
        verified_at=NOW(),verified_by=%s WHERE id=%s""",(reason,admin_id,deposit_id))
        return con.execute("""SELECT u.telegram_id,d.amount FROM deposits d
        JOIN users u ON u.id=d.user_id WHERE d.id=%s""",(deposit_id,)).fetchone()

def create_today_payouts(date_str,rate=10):
    with db() as con:
        users=con.execute("SELECT * FROM users WHERE status='ACTIVE' AND active_amount>0").fetchall()
        out=[]
        for u in users:
            amount=u["active_amount"]; p=amount*rate//100
            con.execute("""INSERT INTO daily_payouts(user_id,payout_date,eligible_amount,payout_amount)
            VALUES(%s,%s,%s,%s) ON CONFLICT(user_id,payout_date) DO NOTHING""",
            (u["id"],date_str,amount,p))
            out.append((u["telegram_id"],p,amount))
        return out

def pending_payouts(date_str):
    with db() as con:
        return con.execute("""SELECT p.*,u.telegram_id,u.username,u.first_name
        FROM daily_payouts p JOIN users u ON u.id=p.user_id
        WHERE p.status='PENDING' AND p.payout_date=%s ORDER BY p.id""",(date_str,)).fetchall()

def mark_payout_paid(payout_id,admin_id,utr,screenshot):
    with db() as con:
        p=con.execute("SELECT * FROM daily_payouts WHERE id=%s FOR UPDATE",(payout_id,)).fetchone()
        if not p or p["status"]!="PENDING": return None
        con.execute("""UPDATE daily_payouts SET status='PAID',utr=%s,screenshot_file_id=%s,
        paid_at=NOW(),confirmed_by=%s WHERE id=%s""",(utr,screenshot,admin_id,payout_id))
        return con.execute("""SELECT u.telegram_id,p.payout_amount,p.payout_date
        FROM daily_payouts p JOIN users u ON u.id=p.user_id WHERE p.id=%s""",(payout_id,)).fetchone()

def get_today_payout_for_user(tid,date_str):
    with db() as con:
        return con.execute("""SELECT p.* FROM daily_payouts p JOIN users u ON u.id=p.user_id
        WHERE u.telegram_id=%s AND p.payout_date=%s""",(tid,date_str)).fetchone()

def create_settlement(tid,today_status,today_amt):
    with db() as con:
        u=con.execute("SELECT * FROM users WHERE telegram_id=%s FOR UPDATE",(tid,)).fetchone()
        if not u: return None
        final=u["active_amount"]+(today_amt if today_status=="UNPAID" else 0)
        cur=con.execute("""INSERT INTO settlements(user_id,original_amount,todays_payout,
        todays_payout_status,final_amount) VALUES(%s,%s,%s,%s,%s) RETURNING id""",
        (u["id"],u["active_amount"],today_amt,today_status,final))
        sid=cur.fetchone()["id"]
        con.execute("UPDATE users SET status='OPT_OUT_PENDING',opt_out_at=NOW() WHERE id=%s",(u["id"],))
        return sid,final,u["active_amount"]

def get_settlement(sid):
    with db() as con:
        return con.execute("""SELECT s.*,u.telegram_id,u.username,u.first_name
        FROM settlements s JOIN users u ON u.id=s.user_id WHERE s.id=%s""",(sid,)).fetchone()

def pending_settlements():
    with db() as con:
        return con.execute("""SELECT s.*,u.telegram_id,u.username,u.first_name
        FROM settlements s JOIN users u ON u.id=s.user_id
        WHERE s.status='PENDING' ORDER BY s.id""").fetchall()

def mark_settlement_paid(sid,admin_id,utr,screenshot):
    with db() as con:
        s=con.execute("SELECT * FROM settlements WHERE id=%s FOR UPDATE",(sid,)).fetchone()
        if not s or s["status"]!="PENDING": return None
        con.execute("""UPDATE settlements SET status='PAID',utr=%s,screenshot_file_id=%s,
        completed_at=NOW(),confirmed_by=%s WHERE id=%s""",(utr,screenshot,admin_id,sid))
        con.execute("UPDATE users SET status='OPTED_OUT',active_amount=0 WHERE id=%s",(s["user_id"],))
        return con.execute("""SELECT u.telegram_id,s.final_amount FROM settlements s
        JOIN users u ON u.id=s.user_id WHERE s.id=%s""",(sid,)).fetchone()


import sqlite3
from contextlib import contextmanager

DATABASE = "bot.db"

@contextmanager
def db():
    con = sqlite3.connect(DATABASE)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def initialize_database():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            active_amount INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            opt_in_at TEXT,
            opt_out_at TEXT
        );

        CREATE TABLE IF NOT EXISTS deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            upi_id TEXT NOT NULL,
            utr TEXT NOT NULL,
            screenshot_file_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            rejection_reason TEXT,
            submitted_at TEXT DEFAULT CURRENT_TIMESTAMP,
            verified_at TEXT,
            verified_by INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS daily_payouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            payout_date TEXT NOT NULL,
            eligible_amount INTEGER NOT NULL,
            payout_amount INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            upi_id TEXT,
            utr TEXT,
            screenshot_file_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            paid_at TEXT,
            confirmed_by INTEGER,
            UNIQUE(user_id, payout_date),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            original_amount INTEGER NOT NULL,
            today's_payout INTEGER NOT NULL DEFAULT 0,
            today's_payout_status TEXT NOT NULL,
            final_amount INTEGER NOT NULL,
            upi_id TEXT,
            utr TEXT,
            screenshot_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            confirmed_by INTEGER,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS admins (
            telegram_id INTEGER PRIMARY KEY,
            name TEXT,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            admin_telegram_id INTEGER,
            action TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """)

def register_user(tid, username, first_name, last_name):
    with db() as con:
        con.execute("""
        INSERT INTO users(telegram_id,username,first_name,last_name)
        VALUES(?,?,?,?)
        ON CONFLICT(telegram_id) DO UPDATE SET
          username=excluded.username,
          first_name=excluded.first_name,
          last_name=excluded.last_name
        """, (tid, username, first_name, last_name))

def get_user(tid):
    with db() as con:
        return con.execute("SELECT * FROM users WHERE telegram_id=?", (tid,)).fetchone()

def set_status(tid, status):
    with db() as con:
        if status == "ACTIVE":
            con.execute("UPDATE users SET status=?,opt_in_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (status,tid))
        elif status == "OPTED_OUT":
            con.execute("UPDATE users SET status=?,opt_out_at=CURRENT_TIMESTAMP WHERE telegram_id=?", (status,tid))
        else:
            con.execute("UPDATE users SET status=? WHERE telegram_id=?", (status,tid))

def add_admin(tid, name="Admin"):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO admins(telegram_id,name) VALUES(?,?)",(tid,name))

def is_admin(tid):
    with db() as con:
        return con.execute("SELECT 1 FROM admins WHERE telegram_id=? AND active=1",(tid,)).fetchone() is not None

def create_deposit(tid, amount, upi_id, utr, screenshot):
    with db() as con:
        u = con.execute("SELECT id FROM users WHERE telegram_id=?",(tid,)).fetchone()
        if not u: return None
        cur = con.execute("""
        INSERT INTO deposits(user_id,amount,upi_id,utr,screenshot_file_id)
        VALUES(?,?,?,?,?)
        """,(u["id"],amount,upi_id,utr,screenshot))
        return cur.lastrowid

def get_deposit(deposit_id):
    with db() as con:
        return con.execute("""
        SELECT d.*,u.telegram_id,u.username,u.first_name,u.active_amount
        FROM deposits d JOIN users u ON u.id=d.user_id WHERE d.id=?
        """,(deposit_id,)).fetchone()

def verify_deposit(deposit_id, admin_id):
    with db() as con:
        d = con.execute("SELECT * FROM deposits WHERE id=?",(deposit_id,)).fetchone()
        if not d or d["status"] != "PENDING": return None
        u = con.execute("SELECT * FROM users WHERE id=?",(d["user_id"],)).fetchone()
        new_amount = u["active_amount"] + d["amount"]
        if new_amount > 10000: return "LIMIT"
        con.execute("UPDATE deposits SET status='VERIFIED',verified_at=CURRENT_TIMESTAMP,verified_by=? WHERE id=?",(admin_id,deposit_id))
        con.execute("UPDATE users SET active_amount=?,status='ACTIVE' WHERE id=?",(new_amount,u["id"]))
        con.execute("INSERT INTO audit_log(user_id,admin_telegram_id,action,old_value,new_value) VALUES(?,?,?,?,?)",
                    (u["id"],admin_id,"DEPOSIT_VERIFIED",str(u["active_amount"]),str(new_amount)))
        return {"telegram_id":u["telegram_id"],"amount":d["amount"],"active_amount":new_amount}

def reject_deposit(deposit_id, admin_id, reason):
    with db() as con:
        d = con.execute("SELECT * FROM deposits WHERE id=?",(deposit_id,)).fetchone()
        if not d or d["status"] != "PENDING": return None
        con.execute("UPDATE deposits SET status='REJECTED',rejection_reason=?,verified_at=CURRENT_TIMESTAMP,verified_by=? WHERE id=?",(reason,admin_id,deposit_id))
        return con.execute("SELECT u.telegram_id,d.amount FROM deposits d JOIN users u ON u.id=d.user_id WHERE d.id=?",(deposit_id,)).fetchone()

def pending_deposits():
    with db() as con:
        return con.execute("""
        SELECT d.*,u.telegram_id,u.username,u.first_name
        FROM deposits d JOIN users u ON u.id=d.user_id
        WHERE d.status='PENDING' ORDER BY d.id
        """).fetchall()

def create_today_payouts(date_str, rate=10):
    with db() as con:
        users = con.execute("SELECT * FROM users WHERE status='ACTIVE' AND active_amount>0").fetchall()
        created=[]
        for u in users:
            payout = u["active_amount"] * rate // 100
            con.execute("""
            INSERT OR IGNORE INTO daily_payouts(user_id,payout_date,eligible_amount,payout_amount,upi_id)
            VALUES(?,?,?,?,?)
            """,(u["id"],date_str,u["active_amount"],payout,None))
            created.append((u["telegram_id"],payout,u["active_amount"]))
        return created

def pending_payouts(date_str=None):
    with db() as con:
        if date_str:
            return con.execute("""
            SELECT p.*,u.telegram_id,u.username,u.first_name
            FROM daily_payouts p JOIN users u ON u.id=p.user_id
            WHERE p.status='PENDING' AND p.payout_date=? ORDER BY p.id
            """,(date_str,)).fetchall()
        return con.execute("""
        SELECT p.*,u.telegram_id,u.username,u.first_name
        FROM daily_payouts p JOIN users u ON u.id=p.user_id
        WHERE p.status='PENDING' ORDER BY p.payout_date,p.id
        """).fetchall()

def mark_payout_paid(payout_id, admin_id, utr, screenshot):
    with db() as con:
        p=con.execute("SELECT * FROM daily_payouts WHERE id=?",(payout_id,)).fetchone()
        if not p or p["status"]!="PENDING": return None
        con.execute("""
        UPDATE daily_payouts SET status='PAID',utr=?,screenshot_file_id=?,paid_at=CURRENT_TIMESTAMP,confirmed_by=?
        WHERE id=?
        """,(utr,screenshot,admin_id,payout_id))
        return con.execute("SELECT u.telegram_id,p.payout_amount,p.payout_date FROM daily_payouts p JOIN users u ON u.id=p.user_id WHERE p.id=?",(payout_id,)).fetchone()

def get_today_payout_for_user(tid, date_str):
    with db() as con:
        return con.execute("""
        SELECT p.* FROM daily_payouts p JOIN users u ON u.id=p.user_id
        WHERE u.telegram_id=? AND p.payout_date=?
        """,(tid,date_str)).fetchone()

def create_settlement(tid, today_status, today_payout):
    with db() as con:
        u=con.execute("SELECT * FROM users WHERE telegram_id=?",(tid,)).fetchone()
        if not u: return None
        final=u["active_amount"] + (today_payout if today_status=="UNPAID" else 0)
        cur=con.execute("""
        INSERT INTO settlements(user_id,original_amount,today's_payout,today's_payout_status,final_amount,upi_id)
        VALUES(?,?,?,?,?,?)
        """,(u["id"],u["active_amount"],today_payout,today_status,final,None))
        con.execute("UPDATE users SET status='OPT_OUT_PENDING' WHERE id=?",(u["id"],))
        return cur.lastrowid, final, u["active_amount"]

def get_settlement(sid):
    with db() as con:
        return con.execute("""
        SELECT s.*,u.telegram_id,u.username,u.first_name FROM settlements s JOIN users u ON u.id=s.user_id WHERE s.id=?
        """,(sid,)).fetchone()

def mark_settlement_paid(sid, admin_id, utr, screenshot):
    with db() as con:
        s=con.execute("SELECT * FROM settlements WHERE id=?",(sid,)).fetchone()
        if not s or s["status"]!="PENDING": return None
        con.execute("""
        UPDATE settlements SET status='PAID',utr=?,screenshot_file_id=?,completed_at=CURRENT_TIMESTAMP,confirmed_by=?
        WHERE id=?
        """,(utr,screenshot,admin_id,sid))
        con.execute("UPDATE users SET status='OPTED_OUT',active_amount=0 WHERE id=?",(s["user_id"],))
        return con.execute("SELECT u.telegram_id,s.final_amount FROM settlements s JOIN users u ON u.id=s.user_id WHERE s.id=?",(sid,)).fetchone()

def pending_settlements():
    with db() as con:
        return con.execute("""
        SELECT s.*,u.telegram_id,u.username,u.first_name
        FROM settlements s JOIN users u ON u.id=s.user_id
        WHERE s.status='PENDING' ORDER BY s.id
        """).fetchall()

def add_audit(user_id, admin_id, action, old_value="", new_value=""):
    with db() as con:
        con.execute("""
        INSERT INTO audit_log(user_id,admin_telegram_id,action,old_value,new_value)
        VALUES(?,?,?,?,?)
        """,(user_id,admin_id,action,old_value,new_value))

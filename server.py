"""
Amar Veggies - Local SQLite Backend API

Run once:
    pip install -r requirements.txt

Start backend:
    python server.py

Local API:
    http://localhost:8000/api

Data is stored locally in:
    amar_veggies.db
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import uuid
import os
import json
import sqlite3
import base64
import random
import re

# ── Config ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "amar-veggies-local-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

DB_PATH = os.getenv("DB_PATH", "amar_veggies.db")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@amarveggies.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
SHOP_LAT = os.getenv("SHOP_LAT", "")
SHOP_LNG = os.getenv("SHOP_LNG", "")

# ── App ───────────────────────────────────────────────────────────
app = FastAPI(title="Amar Veggies Local API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── SQLite helpers ────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def now_iso():
    return datetime.utcnow().isoformat()

def normalize_email(email: Optional[str]):
    if not email:
        return None
    return email.strip().lower()

def normalize_phone(phone: Optional[str]):
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits[-10:]
    return digits or None

def make_otp():
    return str(random.randint(100000, 999999))

def public_user(user):
    data = {k: user.get(k) for k in ("id", "name", "email", "phone", "is_admin") if k in user}
    email = data.get("email") or ""
    if email.startswith("phone_") and email.endswith("@mobile.local"):
        email = ""
    data["email"] = email
    data["phone"] = data.get("phone") or ""
    data["is_admin"] = bool(data.get("is_admin"))
    return data

def row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("items", "timeline", "quantity_options"):
        if key in d and isinstance(d[key], str):
            try:
                d[key] = json.loads(d[key])
            except Exception:
                pass
    if "is_admin" in d:
        d["is_admin"] = bool(d["is_admin"])
    if "available" in d:
        d["available"] = bool(d["available"])
    if "featured" in d:
        d["featured"] = bool(d["featured"])
    return d

def rows_to_list(rows):
    return [row_to_dict(r) for r in rows]

def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE,
            phone TEXT UNIQUE,
            password TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)
    # Lightweight migrations for older local databases
    existing_user_cols = [r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "phone" not in existing_user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN phone TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            id TEXT PRIMARY KEY,
            email TEXT,
            phone TEXT,
            otp TEXT NOT NULL,
            purpose TEXT NOT NULL DEFAULT 'register',
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            emoji TEXT DEFAULT '🌿',
            category TEXT NOT NULL,
            price REAL NOT NULL,
            unit TEXT NOT NULL,
            stock INTEGER NOT NULL,
            available INTEGER NOT NULL DEFAULT 1,
            featured INTEGER NOT NULL DEFAULT 0,
            quantity_options TEXT NOT NULL DEFAULT '[100,250,500,1000]',
            image_data TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            user_name TEXT NOT NULL,
            user_email TEXT NOT NULL,
            items TEXT NOT NULL,
            address TEXT NOT NULL,
            phone TEXT NOT NULL,
            slot TEXT NOT NULL,
            notes TEXT DEFAULT '',
            delivery_lat REAL,
            delivery_lng REAL,
            delivery_place_id TEXT DEFAULT '',
            delivery_maps_url TEXT DEFAULT '',
            subtotal REAL NOT NULL,
            delivery REAL NOT NULL,
            total REAL NOT NULL,
            payment TEXT NOT NULL DEFAULT 'Cash on Delivery',
            status TEXT NOT NULL DEFAULT 'pending',
            timeline TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    existing_order_cols = [r[1] for r in cur.execute("PRAGMA table_info(orders)").fetchall()]
    order_migrations = {
        "delivery_lat": "ALTER TABLE orders ADD COLUMN delivery_lat REAL",
        "delivery_lng": "ALTER TABLE orders ADD COLUMN delivery_lng REAL",
        "delivery_place_id": "ALTER TABLE orders ADD COLUMN delivery_place_id TEXT DEFAULT ''",
        "delivery_maps_url": "ALTER TABLE orders ADD COLUMN delivery_maps_url TEXT DEFAULT ''",
    }
    for col, ddl in order_migrations.items():
        if col not in existing_order_cols:
            cur.execute(ddl)

    conn.commit()
    conn.close()
    print(f"✅ SQLite ready: {DB_PATH}")

# ── Security ──────────────────────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

def hash_password(p):
    return pwd_ctx.hash(p)

def verify_password(p, h):
    return pwd_ctx.verify(p, h)

def create_token(data: dict):
    exp = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def get_user_by_id(user_id: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row_to_dict(user)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_admin(user=Depends(get_current_user)):
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# ── Schemas ───────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str

class LoginIn(BaseModel):
    # User can log in with either Gmail/email or mobile number as their user ID.
    identifier: Optional[str] = None
    email: Optional[str] = None  # kept for backward compatibility
    phone: Optional[str] = None
    password: str

class SendOtpIn(BaseModel):
    name: Optional[str] = ""
    email: Optional[str] = None
    phone: Optional[str] = None

class VerifyOtpRegisterIn(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    otp: str
    password: str

class SendLoginOtpIn(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None

class VerifyOtpLoginIn(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None
    otp: str

class ForgotPasswordSendIn(BaseModel):
    identifier: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class ForgotPasswordResetIn(BaseModel):
    identifier: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    otp: str
    password: str

class ProductIn(BaseModel):
    name: str
    description: Optional[str] = ""
    emoji: Optional[str] = "🌿"
    category: str
    price: float
    unit: str
    stock: int
    available: Optional[bool] = True
    featured: Optional[bool] = False
    quantity_options: Optional[List[int]] = [100, 250, 500, 1000]

class CartItemIn(BaseModel):
    product_id: str
    quantity: int
    selected_weight: int

class OrderIn(BaseModel):
    items: List[CartItemIn]
    address: str
    phone: str
    slot: str
    notes: Optional[str] = ""
    delivery_lat: Optional[float] = None
    delivery_lng: Optional[float] = None
    delivery_place_id: Optional[str] = ""

class OrderStatusIn(BaseModel):
    status: str

# ── Seed Admin ────────────────────────────────────────────────────
def seed_admin():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("⚠️ Admin env vars missing, skipping seed admin")
        return
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_EMAIL,)).fetchone()
    if not existing:
        conn.execute(
            "INSERT INTO users (id, name, email, phone, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), "Admin", ADMIN_EMAIL, None, hash_password(ADMIN_PASSWORD), 1, now_iso()),
        )
        conn.commit()
        print("✅ Seeded admin")
    else:
        print("✅ Admin already exists")
    conn.close()

init_db()
seed_admin()


def split_identifier(identifier: Optional[str] = None, email: Optional[str] = None, phone: Optional[str] = None):
    value = (identifier or "").strip()
    if value and "@" in value:
        email = value
    elif value:
        phone = value
    email = normalize_email(email)
    phone = normalize_phone(phone)
    return email, phone

def get_user_by_email_or_phone(conn, email: Optional[str], phone: Optional[str]):
    if email:
        return conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if phone:
        return conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    return None

def make_maps_url(lat: Optional[float], lng: Optional[float], address: Optional[str] = None):
    if lat is not None and lng is not None:
        return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    if address:
        from urllib.parse import quote_plus
        return f"https://www.google.com/maps/search/?api=1&query={quote_plus(address)}"
    return ""

def make_directions_url(lat: Optional[float], lng: Optional[float], address: Optional[str] = None):
    destination = f"{lat},{lng}" if lat is not None and lng is not None else (address or "")
    if not destination:
        return ""
    from urllib.parse import quote_plus
    origin = ""
    if SHOP_LAT and SHOP_LNG:
        origin = f"&origin={quote_plus(SHOP_LAT + ',' + SHOP_LNG)}"
    return f"https://www.google.com/maps/dir/?api=1{origin}&destination={quote_plus(destination)}&travelmode=driving"

# ── Auth ──────────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register(body: RegisterIn):
    # Old password registration is kept for admin/backward compatibility.
    # New customers should use /send-otp and /verify-otp-register.
    email = normalize_email(body.email)
    if not email:
        raise HTTPException(400, "Email is required")
    conn = get_db()
    if conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    user = {
        "id": str(uuid.uuid4()),
        "name": body.name.strip(),
        "email": email,
        "phone": "",
        "password": hash_password(body.password),
        "is_admin": False,
        "created_at": now_iso(),
    }
    conn.execute(
        "INSERT INTO users (id, name, email, phone, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], user["name"], user["email"], None, user["password"], 0, user["created_at"]),
    )
    conn.commit()
    conn.close()
    token = create_token({"sub": user["id"]})
    return {"token": token, "user": public_user(user)}

@app.post("/api/auth/send-otp")
def send_otp(body: SendOtpIn):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)

    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")

    conn = get_db()
    if email and conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    if phone and conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone():
        conn.close()
        raise HTTPException(400, "Mobile number already registered")

    otp = make_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()

    if email:
        conn.execute("DELETE FROM otps WHERE email = ? AND purpose = 'register'", (email,))
    if phone:
        conn.execute("DELETE FROM otps WHERE phone = ? AND purpose = 'register'", (phone,))

    conn.execute(
        "INSERT INTO otps (id, email, phone, otp, purpose, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), email, phone, otp, "register", expires_at, now_iso()),
    )
    conn.commit()
    conn.close()

    # Local development: the OTP is returned so you can test without paying for SMS/email services.
    # In production, send this OTP using an email/SMS provider and remove dev_otp from the response.
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

@app.post("/api/auth/verify-otp-register")
def verify_otp_register(body: VerifyOtpRegisterIn):
    name = body.name.strip()
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    otp = body.otp.strip()
    password = body.password.strip()

    if not name:
        raise HTTPException(400, "Full name is required")
    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    conn = get_db()
    if email and conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        conn.close()
        raise HTTPException(400, "Email already registered")
    if phone and conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone():
        conn.close()
        raise HTTPException(400, "Mobile number already registered")

    if email:
        row = conn.execute(
            "SELECT * FROM otps WHERE email = ? AND purpose = 'register' ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM otps WHERE phone = ? AND purpose = 'register' ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()

    otp_doc = row_to_dict(row)
    if not otp_doc:
        conn.close()
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_doc["expires_at"]) < datetime.utcnow():
        conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
        conn.commit()
        conn.close()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_doc["otp"] != otp:
        conn.close()
        raise HTTPException(400, "Invalid OTP")

    stored_email = email or f"phone_{phone}@mobile.local"
    user = {
        "id": str(uuid.uuid4()),
        "name": name,
        "email": stored_email,
        "phone": phone or "",
        "password": hash_password(password),
        "is_admin": False,
        "created_at": now_iso(),
    }
    conn.execute(
        "INSERT INTO users (id, name, email, phone, password, is_admin, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user["id"], user["name"], stored_email, phone, user["password"], 0, user["created_at"]),
    )
    conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
    conn.commit()
    conn.close()

    token = create_token({"sub": user["id"]})
    return {"token": token, "user": public_user(user)}

@app.post("/api/auth/send-login-otp")
def send_login_otp(body: SendLoginOtpIn):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)

    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")

    conn = get_db()
    if email:
        user_exists = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    else:
        user_exists = conn.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()

    if not user_exists:
        conn.close()
        raise HTTPException(404, "Account not found. Please register first")

    otp = make_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()

    if email:
        conn.execute("DELETE FROM otps WHERE email = ? AND purpose = 'login'", (email,))
    if phone:
        conn.execute("DELETE FROM otps WHERE phone = ? AND purpose = 'login'", (phone,))

    conn.execute(
        "INSERT INTO otps (id, email, phone, otp, purpose, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), email, phone, otp, "login", expires_at, now_iso()),
    )
    conn.commit()
    conn.close()

    # Local development: shown to you for testing. Replace with SMS/email sending in production.
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

@app.post("/api/auth/verify-otp-login")
def verify_otp_login(body: VerifyOtpLoginIn):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    otp = body.otp.strip()

    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")

    conn = get_db()
    if email:
        user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        otp_row = conn.execute(
            "SELECT * FROM otps WHERE email = ? AND purpose = 'login' ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
    else:
        user_row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
        otp_row = conn.execute(
            "SELECT * FROM otps WHERE phone = ? AND purpose = 'login' ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()

    user = row_to_dict(user_row)
    otp_doc = row_to_dict(otp_row)
    if not user:
        conn.close()
        raise HTTPException(404, "Account not found. Please register first")
    if not otp_doc:
        conn.close()
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_doc["expires_at"]) < datetime.utcnow():
        conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
        conn.commit()
        conn.close()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_doc["otp"] != otp:
        conn.close()
        raise HTTPException(400, "Invalid OTP")

    conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
    conn.commit()
    conn.close()

    token = create_token({"sub": user["id"]})
    return {"token": token, "user": public_user(user)}

@app.post("/api/auth/login")
def login(body: LoginIn):
    identifier = (body.identifier or body.email or body.phone or "").strip()
    if not identifier:
        raise HTTPException(400, "Enter your email or mobile number")

    email = normalize_email(identifier) if "@" in identifier else None
    phone = normalize_phone(identifier) if "@" not in identifier else None

    conn = get_db()
    if email:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()

    user = row_to_dict(row)
    if not user or not user.get("password") or not verify_password(body.password, user["password"]):
        raise HTTPException(401, "Invalid email/mobile number or password")
    token = create_token({"sub": user["id"]})
    return {"token": token, "user": public_user(user)}


@app.post("/api/auth/forgot-password/send-otp")
def forgot_password_send_otp(body: ForgotPasswordSendIn):
    email, phone = split_identifier(body.identifier, body.email, body.phone)
    if not email and not phone:
        raise HTTPException(400, "Enter your email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")

    conn = get_db()
    user_row = get_user_by_email_or_phone(conn, email, phone)
    if not user_row:
        conn.close()
        raise HTTPException(404, "Account not found")

    otp = make_otp()
    expires_at = (datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat()
    if email:
        conn.execute("DELETE FROM otps WHERE email = ? AND purpose = 'reset_password'", (email,))
    if phone:
        conn.execute("DELETE FROM otps WHERE phone = ? AND purpose = 'reset_password'", (phone,))
    conn.execute(
        "INSERT INTO otps (id, email, phone, otp, purpose, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), email, phone, otp, "reset_password", expires_at, now_iso()),
    )
    conn.commit()
    conn.close()

    # Local development: show OTP for testing. In production, send this through email/SMS and remove dev_otp.
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

@app.post("/api/auth/forgot-password/reset")
def forgot_password_reset(body: ForgotPasswordResetIn):
    email, phone = split_identifier(body.identifier, body.email, body.phone)
    otp = body.otp.strip()
    password = body.password.strip()
    if not email and not phone:
        raise HTTPException(400, "Enter your email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")

    conn = get_db()
    user_row = get_user_by_email_or_phone(conn, email, phone)
    if not user_row:
        conn.close()
        raise HTTPException(404, "Account not found")

    if email:
        otp_row = conn.execute(
            "SELECT * FROM otps WHERE email = ? AND purpose = 'reset_password' ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
    else:
        otp_row = conn.execute(
            "SELECT * FROM otps WHERE phone = ? AND purpose = 'reset_password' ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
    otp_doc = row_to_dict(otp_row)
    if not otp_doc:
        conn.close()
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_doc["expires_at"]) < datetime.utcnow():
        conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
        conn.commit()
        conn.close()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_doc["otp"] != otp:
        conn.close()
        raise HTTPException(400, "Invalid OTP")

    user = row_to_dict(user_row)
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(password), user["id"]))
    conn.execute("DELETE FROM otps WHERE id = ?", (otp_doc["id"],))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Password reset successful"}

@app.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    return public_user(user)

# ── Products ──────────────────────────────────────────────────────
@app.get("/api/products")
def list_products(category: Optional[str] = None, search: Optional[str] = None, featured: Optional[bool] = None):
    query = "SELECT * FROM products WHERE 1=1"
    params = []
    if category and category != "All":
        query += " AND category = ?"
        params.append(category)
    if search:
        query += " AND LOWER(name) LIKE ?"
        params.append(f"%{search.lower()}%")
    if featured is not None:
        query += " AND featured = ?"
        params.append(1 if featured else 0)
    query += " ORDER BY created_at DESC"
    conn = get_db()
    products = conn.execute(query, params).fetchall()
    conn.close()
    return rows_to_list(products)

@app.get("/api/products/{pid}")
def get_product(pid: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Product not found")
    return row_to_dict(row)

@app.post("/api/products", dependencies=[Depends(require_admin)])
def create_product(body: ProductIn):
    p = body.dict()
    p["id"] = str(uuid.uuid4())
    p["created_at"] = now_iso()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO products
        (id, name, description, emoji, category, price, unit, stock, available, featured, quantity_options, image_data, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p["id"], p["name"], p.get("description", ""), p.get("emoji", "🌿"), p["category"],
            p["price"], p["unit"], p["stock"], 1 if p.get("available") else 0,
            1 if p.get("featured") else 0, json.dumps(p.get("quantity_options") or [100, 250, 500, 1000]),
            None, p["created_at"]
        ),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM products WHERE id = ?", (p["id"],)).fetchone()
    conn.close()
    return row_to_dict(saved)

@app.put("/api/products/{pid}", dependencies=[Depends(require_admin)])
def update_product(pid: str, body: ProductIn):
    conn = get_db()
    if not conn.execute("SELECT id FROM products WHERE id = ?", (pid,)).fetchone():
        conn.close()
        raise HTTPException(404, "Product not found")
    p = body.dict()
    conn.execute(
        """
        UPDATE products SET
            name = ?, description = ?, emoji = ?, category = ?, price = ?, unit = ?, stock = ?,
            available = ?, featured = ?, quantity_options = ?
        WHERE id = ?
        """,
        (
            p["name"], p.get("description", ""), p.get("emoji", "🌿"), p["category"], p["price"],
            p["unit"], p["stock"], 1 if p.get("available") else 0, 1 if p.get("featured") else 0,
            json.dumps(p.get("quantity_options") or [100, 250, 500, 1000]), pid
        ),
    )
    conn.commit()
    saved = conn.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    conn.close()
    return row_to_dict(saved)

@app.delete("/api/products/{pid}", dependencies=[Depends(require_admin)])
def delete_product(pid: str):
    conn = get_db()
    cur = conn.execute("DELETE FROM products WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Product not found")
    return {"ok": True}

# ── Orders ────────────────────────────────────────────────────────
ORDER_STATUSES = ["pending", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled"]

@app.post("/api/orders")
def create_order(body: OrderIn, user=Depends(get_current_user)):
    conn = get_db()
    items_detail = []
    subtotal = 0
    for ci in body.items:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (ci.product_id,)).fetchone()
        p = row_to_dict(row)
        if not p:
            conn.close()
            raise HTTPException(400, f"Product {ci.product_id} not found")
        if not p.get("available"):
            conn.close()
            raise HTTPException(400, f"{p['name']} is unavailable")
        if ci.selected_weight not in p.get("quantity_options", [100, 250, 500, 1000]):
            conn.close()
            raise HTTPException(400, f"Invalid weight option for {p['name']}")
        weight = ci.selected_weight or 1000
        line_total = round(p["price"] * (weight / 1000) * ci.quantity, 2)
        subtotal += line_total
        items_detail.append({
            "product_id": ci.product_id,
            "name": p["name"],
            "emoji": p.get("emoji", "🌿"),
            "price": p["price"],
            "unit": p["unit"],
            "quantity": ci.quantity,
            "line_total": line_total,
            "selected_weight": ci.selected_weight,
        })
    delivery = 0 if subtotal >= 300 else 40
    order = {
        "id": str(uuid.uuid4()),
        "user_id": user["id"],
        "user_name": user["name"],
        "user_email": public_user(user).get("email", ""),
        "items": items_detail,
        "address": body.address,
        "phone": body.phone,
        "slot": body.slot,
        "notes": body.notes or "",
        "delivery_lat": body.delivery_lat,
        "delivery_lng": body.delivery_lng,
        "delivery_place_id": body.delivery_place_id or "",
        "delivery_maps_url": make_maps_url(body.delivery_lat, body.delivery_lng, body.address),
        "delivery_directions_url": make_directions_url(body.delivery_lat, body.delivery_lng, body.address),
        "subtotal": round(subtotal, 2),
        "delivery": delivery,
        "total": round(subtotal + delivery, 2),
        "payment": "Cash on Delivery",
        "status": "pending",
        "timeline": [{"status": "pending", "at": now_iso()}],
        "created_at": now_iso(),
    }
    conn.execute(
        """
        INSERT INTO orders
        (id, user_id, user_name, user_email, items, address, phone, slot, notes, delivery_lat, delivery_lng, delivery_place_id, delivery_maps_url, subtotal, delivery, total, payment, status, timeline, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            order["id"], order["user_id"], order["user_name"], order["user_email"], json.dumps(order["items"]),
            order["address"], order["phone"], order["slot"], order["notes"], order["delivery_lat"], order["delivery_lng"],
            order["delivery_place_id"], order["delivery_maps_url"], order["subtotal"], order["delivery"],
            order["total"], order["payment"], order["status"], json.dumps(order["timeline"]), order["created_at"]
        ),
    )
    conn.commit()
    conn.close()
    return order

@app.get("/api/orders")
def list_orders(user=Depends(get_current_user)):
    conn = get_db()
    if user.get("is_admin"):
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return rows_to_list(rows)

@app.get("/api/orders/{oid}")
def get_order(oid: str, user=Depends(get_current_user)):
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    conn.close()
    order = row_to_dict(row)
    if not order:
        raise HTTPException(404, "Order not found")
    if not user.get("is_admin") and order["user_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    return order

@app.put("/api/orders/{oid}/status", dependencies=[Depends(require_admin)])
def update_order_status(oid: str, body: OrderStatusIn):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {ORDER_STATUSES}")
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    order = row_to_dict(row)
    if not order:
        conn.close()
        raise HTTPException(404, "Order not found")
    timeline = order.get("timeline", [])
    timeline.append({"status": body.status, "at": now_iso()})
    conn.execute("UPDATE orders SET status = ?, timeline = ? WHERE id = ?", (body.status, json.dumps(timeline), oid))
    conn.commit()
    saved = conn.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    conn.close()
    return row_to_dict(saved)

# ── Admin stats ───────────────────────────────────────────────────
@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    conn = get_db()
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    pending_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]
    total_products = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    avail_products = conn.execute("SELECT COUNT(*) FROM products WHERE available = 1").fetchone()[0]
    revenue = conn.execute("SELECT COALESCE(SUM(total), 0) FROM orders WHERE status != 'cancelled'").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 0").fetchone()[0]
    conn.close()
    return {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "total_products": total_products,
        "available_products": avail_products,
        "revenue": round(revenue or 0, 2),
        "total_users": total_users,
    }

# ── Health ────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Amar Veggies Local SQLite API", "database": DB_PATH, "shop_location_configured": bool(SHOP_LAT and SHOP_LNG)}

# ── Product images ────────────────────────────────────────────────
@app.post("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
async def upload_product_image(pid: str, file: UploadFile = File(...)):
    conn = get_db()
    row = conn.execute("SELECT id FROM products WHERE id = ?", (pid,)).fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Product not found")
    if not file.content_type or not file.content_type.startswith("image/"):
        conn.close()
        raise HTTPException(400, "File must be an image (jpg, png, webp, etc.)")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        conn.close()
        raise HTTPException(400, "Image must be under 5MB")
    b64 = base64.b64encode(contents).decode("utf-8")
    image_data = f"data:{file.content_type};base64,{b64}"
    conn.execute("UPDATE products SET image_data = ? WHERE id = ?", (image_data, pid))
    conn.commit()
    conn.close()
    return {"ok": True, "image_data": image_data}

@app.delete("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
def delete_product_image(pid: str):
    conn = get_db()
    cur = conn.execute("UPDATE products SET image_data = NULL WHERE id = ?", (pid,))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Product not found")
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="127.0.0.1", port=port, reload=False)

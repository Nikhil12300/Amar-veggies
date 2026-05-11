
"""
Amar Veggies - PostgreSQL/SQLAlchemy Backend API

Production:
    Set DATABASE_URL in Render to your PostgreSQL Internal Database URL.

Run once:
    pip install -r requirements.txt

Start backend:
    python server.py
"""

from fastapi import FastAPI, HTTPException, Depends, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List, Any, Dict
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import create_engine, String, Integer, Float, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session
from sqlalchemy.exc import IntegrityError
from twilio.rest import Client 
import uuid
import os
import json
import base64
import random
import re
import requests
import razorpay
import hmac
import hashlib

# ── Config ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "amar-veggies-local-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./amar_veggies.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace(
        "postgres://",
        "postgresql://",
        1
    )
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
SHOP_LAT = os.getenv("SHOP_LAT", "")
SHOP_LNG = os.getenv("SHOP_LNG", "")
BREVO_API_KEY = os.getenv("BREVO_API_KEY", "")
OTP_EMAIL_FROM = os.getenv("OTP_EMAIL_FROM", "")
OTP_EMAIL_FROM_NAME = os.getenv("OTP_EMAIL_FROM_NAME", "Amar Veggies")
SHOW_DEV_OTP = os.getenv("SHOW_DEV_OTP", "true").lower() == "true"
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER", "")
ADMIN_WHATSAPP_NUMBER = os.getenv("ADMIN_WHATSAPP_NUMBER", "")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

# ── Database ──────────────────────────────────────────────────────
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    password: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_admin: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

class OTP(Base):
    __tablename__ = "otps"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    otp: Mapped[str] = mapped_column(String, nullable=False)
    purpose: Mapped[str] = mapped_column(String, nullable=False, default="register")
    expires_at: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    emoji: Mapped[str] = mapped_column(String, default="🌿")
    category: Mapped[str] = mapped_column(String, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String, nullable=False)
    stock: Mapped[float] = mapped_column(Float, nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    featured: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quantity_options: Mapped[str] = mapped_column(Text, nullable=False, default="[100,250,500,1000]")
    image_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    user_name: Mapped[str] = mapped_column(String, nullable=False)
    user_email: Mapped[str] = mapped_column(String, nullable=False)
    items: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(String, nullable=False)
    slot: Mapped[str] = mapped_column(String, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    delivery_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delivery_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    delivery_place_id: Mapped[str] = mapped_column(Text, default="")
    delivery_maps_url: Mapped[str] = mapped_column(Text, default="")
    delivery_partner: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    delivery: Mapped[float] = mapped_column(Float, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    payment: Mapped[str] = mapped_column(String, nullable=False, default="Cash on Delivery")
    payment_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    razorpay_order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    razorpay_payment_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    timeline: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

def init_db():
    Base.metadata.create_all(bind=engine)

    from sqlalchemy import text

    with engine.connect() as conn:
        migrations = [
            "ALTER TABLE orders ADD COLUMN payment_status VARCHAR(50) DEFAULT 'pending';",
            "ALTER TABLE orders ADD COLUMN razorpay_order_id VARCHAR(255);",
            "ALTER TABLE orders ADD COLUMN razorpay_payment_id VARCHAR(255);",
            "ALTER TABLE orders ADD COLUMN delivery_partner VARCHAR(255);",
        ]

        for migration in migrations:
            try:
                conn.execute(text(migration))
            except Exception as e:
                print(f"Migration skipped: {e}")

    conn.commit()

    print("Database migration completed")

    # Lightweight migration for older databases where products.stock was INTEGER.
    # This lets stock support decimal kg values like 0.5 after 500g orders.
    try:
        with engine.begin() as conn:
            if DATABASE_URL.startswith("postgres"):
                conn.execute(text("ALTER TABLE products ALTER COLUMN stock TYPE DOUBLE PRECISION USING stock::double precision"))
            else:
                # SQLite is dynamically typed, so existing INTEGER columns can store decimals.
                # No table rebuild needed for local development.
                pass
    except Exception as e:
        print(f"⚠️ Stock column migration skipped: {e}")

    print("✅ Database ready")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ── App ───────────────────────────────────────────────────────────
app = FastAPI(title="Amar Veggies API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://amarveggies.netlify.app",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
        "null",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Helpers ───────────────────────────────────────────────────────
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

def build_otp_response(otp: str) -> Dict[str, Any]:
    response: Dict[str, Any] = {
        "ok": True,
        "message": "OTP sent",
        "expires_in_minutes": OTP_EXPIRE_MINUTES,
    }
    if SHOW_DEV_OTP:
        response["dev_otp"] = otp
    return response

def send_email_otp(to_email: str, otp: str, purpose: str = "verification") -> bool:
    if not BREVO_API_KEY or not OTP_EMAIL_FROM:
        print("⚠️ Brevo email config missing. OTP email was not sent.")
        return False

    subject = f"{otp} is your Amar Veggies OTP"
    html_content = f"""
    <div style="font-family:Arial,sans-serif;padding:20px;line-height:1.5">
        <h2 style="color:#1a3d2b;margin-bottom:8px">Amar Veggies</h2>
        <p>Your OTP for {purpose} is:</p>
        <h1 style="letter-spacing:4px;color:#2d6a4f">{otp}</h1>
        <p>This OTP expires in {OTP_EXPIRE_MINUTES} minutes.</p>
        <p style="color:#666;font-size:13px">If you did not request this OTP, you can ignore this email.</p>
    </div>
    """

    payload = {
        "sender": {"name": OTP_EMAIL_FROM_NAME, "email": OTP_EMAIL_FROM},
        "to": [{"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            json=payload,
            headers=headers,
            timeout=15,
        )
        if response.status_code not in (200, 201, 202):
            print("Brevo email failed:", response.status_code, response.text)
            return False
        return True
    except Exception as e:
        print("Email send failed:", e)
        return False

def send_whatsapp_order_notification(order_data: Dict[str, Any]) -> bool:
    if (
        not TWILIO_ACCOUNT_SID
        or not TWILIO_AUTH_TOKEN
        or not TWILIO_WHATSAPP_NUMBER
        or not ADMIN_WHATSAPP_NUMBER
    ):
        print("⚠️ Twilio WhatsApp config missing")
        return False

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        items_text = ""
        for item in order_data.get("items", []):
            items_text += (
                f"• {item.get('name')} "
                f"({item.get('selected_weight')}g × {item.get('quantity')})\n"
            )

        message_body = f"""
🛒 *NEW ORDER RECEIVED*

👤 Customer: {order_data.get('user_name')}
📞 Phone: {order_data.get('phone')}

📍 Address:
{order_data.get('address')}

🧺 Items:
{items_text}

💰 Total: ₹{order_data.get('total')}

🕒 Slot: {order_data.get('slot')}

📝 Notes:
{order_data.get('notes') or 'None'}

━━━━━━━━━━━━━━
Amar Veggies
"""

        client.messages.create(
            body=message_body,
            from_=TWILIO_WHATSAPP_NUMBER,
            to=ADMIN_WHATSAPP_NUMBER,
        )

        print("✅ WhatsApp notification sent")
        return True

    except Exception as e:
        print("⚠️ WhatsApp send failed:", e)
        return False

def send_whatsapp_customer_status(order, status):
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN or not TWILIO_WHATSAPP_NUMBER:
        print("⚠️ Twilio WhatsApp config missing")
        return False

    if not order.phone:
        return False

    status_labels = {
        "pending": "placed",
        "confirmed": "confirmed",
        "out_for_delivery": "out for delivery",
        "delivered": "delivered",
        "cancelled": "cancelled",
    }

    status_text = status_labels.get(status, status.replace("_", " "))

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        client.messages.create(
            body=f"🌿 Amar Veggies Update\n\nYour order #{order.id[-8:].upper()} is now {status_text}.\n\nThank you for ordering!",
            from_=TWILIO_WHATSAPP_NUMBER,
            to=f"whatsapp:+91{normalize_phone(order.phone)}"
        )

        print("✅ Customer WhatsApp status update sent")
        return True

    except Exception as e:
        print("⚠️ Customer WhatsApp update failed:", e)
        return False

def model_to_dict(obj: Any) -> Optional[Dict[str, Any]]:
    if obj is None:
        return None

    table = getattr(obj, "__table__", None)
    if table is None:
        return None

    d: Dict[str, Any] = {c.name: getattr(obj, c.name) for c in table.columns}
    for key in ("items", "timeline", "quantity_options"):
        value = d.get(key)
        if isinstance(value, str):
            try:
                d[key] = json.loads(value)
            except Exception:
                pass

    if "is_admin" in d:
        d["is_admin"] = bool(d["is_admin"])
    if "available" in d:
        d["available"] = bool(d["available"])
    if "featured" in d:
        d["featured"] = bool(d["featured"])
    return d

def models_to_list(rows: List[Any]) -> List[Dict[str, Any]]:
    return [d for d in (model_to_dict(r) for r in rows) if d is not None]

def public_user(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    user = user or {}
    data: Dict[str, Any] = {
        "id": user.get("id"),
        "name": user.get("name") or "",
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "is_admin": bool(user.get("is_admin")),
    }
    email = str(data.get("email") or "")
    if email.startswith("phone_") and email.endswith("@mobile.local"):
        data["email"] = ""
    return data

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

def split_identifier(identifier: Optional[str] = None, email: Optional[str] = None, phone: Optional[str] = None):
    value = (identifier or "").strip()
    if value and "@" in value:
        email = value
    elif value:
        phone = value
    email = normalize_email(email)
    phone = normalize_phone(phone)
    return email, phone

def get_user_by_email_or_phone(db: Session, email: Optional[str], phone: Optional[str]):
    if email:
        return db.query(User).filter(User.email == email).first()
    if phone:
        return db.query(User).filter(User.phone == phone).first()
    return None

# ── Security ──────────────────────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)
razorpay_client: Any = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

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

def get_user_by_id(db: Session, user_id: str) -> Optional[Dict[str, Any]]:
    user = db.query(User).filter(User.id == user_id).first()
    return model_to_dict(user)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="Invalid token")
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# ── Schemas ───────────────────────────────────────────────────────
class RegisterIn(BaseModel):
    name: str
    email: str
    password: str

class LoginIn(BaseModel):
    identifier: Optional[str] = None
    email: Optional[str] = None
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
    stock: float
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

class CreatePaymentOrderIn(BaseModel):
    amount: float

class VerifyPaymentIn(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    order_id: str

class OrderStatusIn(BaseModel):
    status: str

class AssignDeliveryIn(BaseModel):
    delivery_partner: str

# ── Seed Admin ────────────────────────────────────────────────────
def seed_admin():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("⚠️ Admin env vars missing, skipping seed admin")
        return
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == normalize_email(ADMIN_EMAIL)).first()
        if not existing:
            db.add(User(
                id=str(uuid.uuid4()),
                name="Admin",
                email=normalize_email(ADMIN_EMAIL),
                phone=None,
                password=hash_password(ADMIN_PASSWORD),
                is_admin=1,
                created_at=now_iso(),
            ))
            print("✅ Seeded admin")
        else:
            existing.password = hash_password(ADMIN_PASSWORD)
            existing.is_admin = 1
            print("✅ Admin password updated")
        db.commit()
    finally:
        db.close()

init_db()
seed_admin()

# ── Auth ──────────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register(body: RegisterIn, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    if not email:
        raise HTTPException(400, "Email is required")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered")
    user = User(
        id=str(uuid.uuid4()),
        name=body.name.strip(),
        email=email,
        phone=None,
        password=hash_password(body.password),
        is_admin=0,
        created_at=now_iso(),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Email already registered")
    token = create_token({"sub": user.id})
    return {"token": token, "user": public_user(model_to_dict(user))}

@app.post("/api/auth/send-otp")
def send_otp(body: SendOtpIn, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered")
    if phone and db.query(User).filter(User.phone == phone).first():
        raise HTTPException(400, "Mobile number already registered")
    if email:
        db.query(OTP).filter(OTP.email == email, OTP.purpose == "register").delete()
    if phone:
        db.query(OTP).filter(OTP.phone == phone, OTP.purpose == "register").delete()
    otp = make_otp()
    if email:
        sent = send_email_otp(email, otp, "registration")
        if not sent and not SHOW_DEV_OTP:
            raise HTTPException(500, "Could not send OTP email. Please try again later")
    db.add(OTP(
        id=str(uuid.uuid4()),
        email=email,
        phone=phone,
        otp=otp,
        purpose="register",
        expires_at=(datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat(),
        created_at=now_iso(),
    ))
    db.commit()
    return build_otp_response(otp)

@app.post("/api/auth/verify-otp-register")
def verify_otp_register(body: VerifyOtpRegisterIn, db: Session = Depends(get_db)):
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
    if email and db.query(User).filter(User.email == email).first():
        raise HTTPException(400, "Email already registered")
    if phone and db.query(User).filter(User.phone == phone).first():
        raise HTTPException(400, "Mobile number already registered")

    q = db.query(OTP).filter(OTP.purpose == "register")
    q = q.filter(OTP.email == email) if email else q.filter(OTP.phone == phone)
    otp_row = q.order_by(OTP.created_at.desc()).first()
    if not otp_row:
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_row.expires_at) < datetime.utcnow():
        db.delete(otp_row)
        db.commit()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_row.otp != otp:
        raise HTTPException(400, "Invalid OTP")

    stored_email = email or f"phone_{phone}@mobile.local"
    user = User(
        id=str(uuid.uuid4()),
        name=name,
        email=stored_email,
        phone=phone,
        password=hash_password(password),
        is_admin=0,
        created_at=now_iso(),
    )
    db.add(user)
    db.delete(otp_row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, "Account already registered")
    token = create_token({"sub": user.id})
    return {"token": token, "user": public_user(model_to_dict(user))}

@app.post("/api/auth/send-login-otp")
def send_login_otp(body: SendLoginOtpIn, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    user_exists = get_user_by_email_or_phone(db, email, phone)
    if not user_exists:
        raise HTTPException(404, "Account not found. Please register first")
    if email:
        db.query(OTP).filter(OTP.email == email, OTP.purpose == "login").delete()
    if phone:
        db.query(OTP).filter(OTP.phone == phone, OTP.purpose == "login").delete()
    otp = make_otp()
    if email:
        sent = send_email_otp(email, otp, "login")
        if not sent and not SHOW_DEV_OTP:
            raise HTTPException(500, "Could not send OTP email. Please try again later")
    db.add(OTP(
        id=str(uuid.uuid4()),
        email=email,
        phone=phone,
        otp=otp,
        purpose="login",
        expires_at=(datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat(),
        created_at=now_iso(),
    ))
    db.commit()
    return build_otp_response(otp)

@app.post("/api/auth/verify-otp-login")
def verify_otp_login(body: VerifyOtpLoginIn, db: Session = Depends(get_db)):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    otp = body.otp.strip()
    if not email and not phone:
        raise HTTPException(400, "Enter an email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    user = get_user_by_email_or_phone(db, email, phone)
    q = db.query(OTP).filter(OTP.purpose == "login")
    q = q.filter(OTP.email == email) if email else q.filter(OTP.phone == phone)
    otp_row = q.order_by(OTP.created_at.desc()).first()
    if not user:
        raise HTTPException(404, "Account not found. Please register first")
    if not otp_row:
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_row.expires_at) < datetime.utcnow():
        db.delete(otp_row)
        db.commit()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_row.otp != otp:
        raise HTTPException(400, "Invalid OTP")
    db.delete(otp_row)
    db.commit()
    token = create_token({"sub": user.id})
    return {"token": token, "user": public_user(model_to_dict(user))}

@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    identifier = (body.identifier or body.email or body.phone or "").strip()
    if not identifier:
        raise HTTPException(400, "Enter your email or mobile number")
    email = normalize_email(identifier) if "@" in identifier else None
    phone = normalize_phone(identifier) if "@" not in identifier else None
    user = get_user_by_email_or_phone(db, email, phone)
    user_dict = model_to_dict(user)
    if not user_dict or not user_dict.get("password") or not verify_password(body.password, str(user_dict["password"])):
        raise HTTPException(401, "Invalid email/mobile number or password")
    token = create_token({"sub": str(user_dict["id"])})
    return {"token": token, "user": public_user(user_dict)}

@app.post("/api/auth/forgot-password/send-otp")
def forgot_password_send_otp(body: ForgotPasswordSendIn, db: Session = Depends(get_db)):
    email, phone = split_identifier(body.identifier, body.email, body.phone)
    if not email and not phone:
        raise HTTPException(400, "Enter your email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    user = get_user_by_email_or_phone(db, email, phone)
    if not user:
        raise HTTPException(404, "Account not found")
    if email:
        db.query(OTP).filter(OTP.email == email, OTP.purpose == "reset_password").delete()
    if phone:
        db.query(OTP).filter(OTP.phone == phone, OTP.purpose == "reset_password").delete()
    otp = make_otp()
    if email:
        sent = send_email_otp(email, otp, "password reset")
        if not sent and not SHOW_DEV_OTP:
            raise HTTPException(500, "Could not send OTP email. Please try again later")
    db.add(OTP(
        id=str(uuid.uuid4()),
        email=email,
        phone=phone,
        otp=otp,
        purpose="reset_password",
        expires_at=(datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)).isoformat(),
        created_at=now_iso(),
    ))
    db.commit()
    return build_otp_response(otp)

@app.post("/api/auth/forgot-password/reset")
def forgot_password_reset(body: ForgotPasswordResetIn, db: Session = Depends(get_db)):
    email, phone = split_identifier(body.identifier, body.email, body.phone)
    otp = body.otp.strip()
    password = body.password.strip()
    if not email and not phone:
        raise HTTPException(400, "Enter your email or mobile number")
    if phone and len(phone) != 10:
        raise HTTPException(400, "Enter a valid 10-digit mobile number")
    if len(password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    user = get_user_by_email_or_phone(db, email, phone)
    if not user:
        raise HTTPException(404, "Account not found")
    q = db.query(OTP).filter(OTP.purpose == "reset_password")
    q = q.filter(OTP.email == email) if email else q.filter(OTP.phone == phone)
    otp_row = q.order_by(OTP.created_at.desc()).first()
    if not otp_row:
        raise HTTPException(400, "OTP not found. Please request a new OTP")
    if datetime.fromisoformat(otp_row.expires_at) < datetime.utcnow():
        db.delete(otp_row)
        db.commit()
        raise HTTPException(400, "OTP expired. Please request a new OTP")
    if otp_row.otp != otp:
        raise HTTPException(400, "Invalid OTP")
    user.password = hash_password(password)
    db.delete(otp_row)
    db.commit()
    return {"ok": True, "message": "Password reset successful"}

@app.get("/api/auth/me")
def me(user: Dict[str, Any] = Depends(get_current_user)):
    return public_user(user)

# ── Products ──────────────────────────────────────────────────────
@app.get("/api/products")
def list_products(category: Optional[str] = None, search: Optional[str] = None, featured: Optional[bool] = None, db: Session = Depends(get_db)):
    q = db.query(Product)
    if category and category != "All":
        q = q.filter(Product.category == category)
    if search:
        q = q.filter(Product.name.ilike(f"%{search}%"))
    if featured is not None:
        q = q.filter(Product.featured == (1 if featured else 0))
    return models_to_list(q.order_by(Product.created_at.desc()).all())

@app.get("/api/products/{pid}")
def get_product(pid: str, db: Session = Depends(get_db)):
    p = db.query(Product).filter(Product.id == pid).first()
    if not p:
        raise HTTPException(404, "Product not found")
    return model_to_dict(p)

@app.post("/api/products", dependencies=[Depends(require_admin)])
def create_product(body: ProductIn, db: Session = Depends(get_db)):
    p = body.dict()
    product = Product(
        id=str(uuid.uuid4()),
        name=p["name"],
        description=p.get("description", ""),
        emoji=p.get("emoji", "🌿"),
        category=p["category"],
        price=p["price"],
        unit=p["unit"],
        stock=float(p["stock"]),
        available=1 if p.get("available") else 0,
        featured=1 if p.get("featured") else 0,
        quantity_options=json.dumps(p.get("quantity_options") or [100, 250, 500, 1000]),
        image_data=None,
        created_at=now_iso(),
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return model_to_dict(product)

@app.put("/api/products/{pid}", dependencies=[Depends(require_admin)])
def update_product(pid: str, body: ProductIn, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == pid).first()
    if not product:
        raise HTTPException(404, "Product not found")
    p = body.dict()
    product.name = p["name"]
    product.description = p.get("description", "")
    product.emoji = p.get("emoji", "🌿")
    product.category = p["category"]
    product.price = p["price"]
    product.unit = p["unit"]
    product.stock = float(p["stock"])
    product.available = 1 if p.get("available") else 0
    product.featured = 1 if p.get("featured") else 0
    product.quantity_options = json.dumps(p.get("quantity_options") or [100, 250, 500, 1000])
    db.commit()
    db.refresh(product)
    return model_to_dict(product)

@app.delete("/api/products/{pid}", dependencies=[Depends(require_admin)])
def delete_product(pid: str, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == pid).first()
    if not product:
        raise HTTPException(404, "Product not found")
    db.delete(product)
    db.commit()
    return {"ok": True}

@app.post("/api/create-payment-order")
def create_payment_order(
    body: CreatePaymentOrderIn,
    user: Dict[str, Any] = Depends(get_current_user),
):
    amount_paise = int(body.amount * 100)

    payment_order = razorpay_client.order.create({
        "amount": amount_paise,
        "currency": "INR",
        "payment_capture": 1
    })

    return {
        "id": payment_order["id"],
        "amount": payment_order["amount"],
        "currency": payment_order["currency"],
        "key": RAZORPAY_KEY_ID
    }

@app.post("/api/verify-payment")
def verify_payment(
    body: VerifyPaymentIn,
    db: Session = Depends(get_db)
):
    generated_signature = hmac.new(
        bytes(RAZORPAY_KEY_SECRET, "utf-8"),
        bytes(
            f"{body.razorpay_order_id}|{body.razorpay_payment_id}",
            "utf-8"
        ),
        hashlib.sha256
    ).hexdigest()

    if generated_signature != body.razorpay_signature:
        raise HTTPException(400, "Payment verification failed")

    order = db.query(Order).filter(Order.id == body.order_id).first()

    if not order:
        raise HTTPException(404, "Order not found")

    order.payment_status = "paid"
    order.razorpay_order_id = body.razorpay_order_id
    order.razorpay_payment_id = body.razorpay_payment_id
    order.payment = "Online"
    order.status = "confirmed"
    timeline = json.loads(order.timeline or "[]")
    timeline.append({
        "status": "confirmed",
        "at": now_iso()
    })
    order.timeline = json.dumps(timeline)

    db.commit()

    return {
        "ok": True,
        "message": "Payment verified"
    }

# ── Orders ────────────────────────────────────────────────────────
ORDER_STATUSES = ["pending", "confirmed", "out_for_delivery", "delivered", "cancelled"]

@app.post("/api/orders")
def create_order(body: OrderIn, user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    items_detail = []
    subtotal = 0
    for ci in body.items:
        product = db.query(Product).filter(Product.id == ci.product_id).first()
        if product is None:
            raise HTTPException(400, f"Product {ci.product_id} not found")
        p = model_to_dict(product)
        if not p:
            raise HTTPException(400, f"Product {ci.product_id} not found")
        if not p.get("available"):
            raise HTTPException(400, f"{p['name']} is unavailable")
        if ci.quantity <= 0:
            raise HTTPException(400, f"Invalid quantity for {p['name']}")
        if ci.selected_weight not in p.get("quantity_options", [100, 250, 500, 1000]):
            raise HTTPException(400, f"Invalid weight option for {p['name']}")

        weight = ci.selected_weight or 1000
        stock_needed = round((weight / 1000) * ci.quantity, 3)
        current_stock = float(product.stock or 0)

        if current_stock < stock_needed:
            raise HTTPException(
                400,
                f"Only {current_stock:g} kg stock available for {p['name']}"
            )

        line_total = round(p["price"] * stock_needed, 2)
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
            "stock_deducted_kg": stock_needed,
        })

        product.stock = round(current_stock - stock_needed, 3)
        if product.stock <= 0:
            product.stock = 0
            product.available = 0

    delivery = 0 if subtotal >= 300 else 40
    timeline = [{"status": "pending", "at": now_iso()}]
    order = Order(
        id=str(uuid.uuid4()),
        user_id=user["id"],
        user_name=user["name"],
        user_email=public_user(user).get("email", ""),
        items=json.dumps(items_detail),
        address=body.address,
        phone=body.phone,
        slot=body.slot,
        notes=body.notes or "",
        delivery_lat=body.delivery_lat,
        delivery_lng=body.delivery_lng,
        delivery_place_id=body.delivery_place_id or "",
        delivery_maps_url=make_maps_url(body.delivery_lat, body.delivery_lng, body.address),
        subtotal=round(subtotal, 2),
        delivery=delivery,
        total=round(subtotal + delivery, 2),
        payment="Cash on Delivery",
        payment_status="cod_pending",
        status="pending",
        timeline=json.dumps(timeline),
        created_at=now_iso(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    try:
        send_whatsapp_order_notification({
            "user_name": user["name"],
            "phone": body.phone,
            "address": body.address,
            "items": items_detail,
            "total": round(subtotal + delivery, 2),
            "slot": body.slot,
            "notes": body.notes or "",
        })
    except Exception as e:
        print("⚠️ WhatsApp notification error:", e)
    result = model_to_dict(order) or {}
    result["delivery_directions_url"] = make_directions_url(body.delivery_lat, body.delivery_lng, body.address)
    return result

@app.get("/api/orders")
def list_orders(user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.get("is_admin"):
        rows = db.query(Order).order_by(Order.created_at.desc()).all()
    else:
        rows = db.query(Order).filter(Order.user_id == user["id"]).order_by(Order.created_at.desc()).all()
    return models_to_list(rows)

@app.get("/api/orders/{oid}")
def get_order(oid: str, user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == oid).first()
    order_dict = model_to_dict(order)
    if not order_dict:
        raise HTTPException(404, "Order not found")
    if not user.get("is_admin") and order_dict.get("user_id") != user.get("id"):
        raise HTTPException(403, "Access denied")
    return order_dict

@app.put("/api/orders/{oid}/status", dependencies=[Depends(require_admin)])
def update_order_status(oid: str, body: OrderStatusIn, db: Session = Depends(get_db)):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {ORDER_STATUSES}")

    order = db.query(Order).filter(Order.id == oid).first()
    if not order:
        raise HTTPException(404, "Order not found")

    old_status = str(order.status or "")
    order_dict = model_to_dict(order) or {}

    # If an order is cancelled after stock was deducted, restore that stock once.
    # This block is protected by old_status != "cancelled" to prevent double restore.
    if body.status == "cancelled" and old_status != "cancelled":
        order_items = order_dict.get("items", [])
        if isinstance(order_items, list):
            for item in order_items:
                if not isinstance(item, dict):
                    continue

                product_id = item.get("product_id")
                if not product_id:
                    continue

                product = db.query(Product).filter(Product.id == str(product_id)).first()
                if not product:
                    continue

                restored_stock = item.get("stock_deducted_kg")
                if restored_stock is None:
                    selected_weight = float(item.get("selected_weight") or 1000)
                    quantity = int(item.get("quantity") or 0)
                    restored_stock = round((selected_weight / 1000) * quantity, 3)

                product.stock = round(float(product.stock or 0) + float(restored_stock), 3)
                if float(product.stock or 0) > 0:
                    product.available = 1

    timeline = order_dict.get("timeline", [])
    if not isinstance(timeline, list):
        timeline = []
    timeline.append({"status": body.status, "at": now_iso()})

    order.status = body.status
    order.timeline = json.dumps(timeline)
    db.commit()
    db.refresh(order)

    try:
        send_whatsapp_customer_status(order, body.status)
    except Exception as e:
        print("⚠️ Customer WhatsApp notification error:", e)

    return model_to_dict(order)

@app.put("/api/orders/{oid}/assign", dependencies=[Depends(require_admin)])
def assign_delivery_partner(
    oid: str,
    body: AssignDeliveryIn,
    db: Session = Depends(get_db)
):
    order = db.query(Order).filter(Order.id == oid).first()

    if not order:
        raise HTTPException(404, "Order not found")

    partner = body.delivery_partner.strip()

    if not partner:
        raise HTTPException(400, "Delivery partner is required")

    order.delivery_partner = partner

    db.commit()
    db.refresh(order)

    return model_to_dict(order)

# ── Admin stats ───────────────────────────────────────────────────
@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats(db: Session = Depends(get_db)):
    total_orders = db.query(Order).count()
    pending_orders = db.query(Order).filter(Order.status == "pending").count()
    total_products = db.query(Product).count()
    avail_products = db.query(Product).filter(Product.available == 1).count()
    total_users = db.query(User).filter(User.is_admin == 0).count()
    revenue = sum([o.total or 0 for o in db.query(Order).filter(Order.status != "cancelled").all()])
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
    db_type = "postgresql" if DATABASE_URL.startswith("postgres") else "sqlite"
    return {
        "status": "ok",
        "service": "Amar Veggies SQLAlchemy API",
        "database": db_type,
        "shop_location_configured": bool(SHOP_LAT and SHOP_LNG),
    }

# ── Product images ────────────────────────────────────────────────
@app.post("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
async def upload_product_image(pid: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == pid).first()
    if not product:
        raise HTTPException(404, "Product not found")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image (jpg, png, webp, etc.)")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 5MB")
    b64 = base64.b64encode(contents).decode("utf-8")
    image_data = f"data:{file.content_type};base64,{b64}"
    product.image_data = image_data
    db.commit()
    return {"ok": True, "image_data": image_data}

@app.delete("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
def delete_product_image(pid: str, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == pid).first()
    if not product:
        raise HTTPException(404, "Product not found")
    product.image_data = None
    db.commit()
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)

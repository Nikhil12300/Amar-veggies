
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
from typing import Optional, List
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import create_engine, Column, String, Integer, Float, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.exc import IntegrityError
import uuid
import os
import json
import base64
import random
import re

# ── Config ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "amar-veggies-local-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./amar_veggies.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
OTP_EXPIRE_MINUTES = int(os.getenv("OTP_EXPIRE_MINUTES", "10"))
SHOP_LAT = os.getenv("SHOP_LAT", "")
SHOP_LNG = os.getenv("SHOP_LNG", "")

# ── Database ──────────────────────────────────────────────────────
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=True)
    phone = Column(String, unique=True, nullable=True)
    password = Column(Text, nullable=True)
    is_admin = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False)

class OTP(Base):
    __tablename__ = "otps"
    id = Column(String, primary_key=True)
    email = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    otp = Column(String, nullable=False)
    purpose = Column(String, nullable=False, default="register")
    expires_at = Column(String, nullable=False)
    created_at = Column(String, nullable=False)

class Product(Base):
    __tablename__ = "products"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    emoji = Column(String, default="🌿")
    category = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    stock = Column(Integer, nullable=False)
    available = Column(Integer, nullable=False, default=1)
    featured = Column(Integer, nullable=False, default=0)
    quantity_options = Column(Text, nullable=False, default="[100,250,500,1000]")
    image_data = Column(Text, nullable=True)
    created_at = Column(String, nullable=False)

class Order(Base):
    __tablename__ = "orders"
    id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    user_name = Column(String, nullable=False)
    user_email = Column(String, nullable=False)
    items = Column(Text, nullable=False)
    address = Column(Text, nullable=False)
    phone = Column(String, nullable=False)
    slot = Column(String, nullable=False)
    notes = Column(Text, default="")
    delivery_lat = Column(Float, nullable=True)
    delivery_lng = Column(Float, nullable=True)
    delivery_place_id = Column(Text, default="")
    delivery_maps_url = Column(Text, default="")
    subtotal = Column(Float, nullable=False)
    delivery = Column(Float, nullable=False)
    total = Column(Float, nullable=False)
    payment = Column(String, nullable=False, default="Cash on Delivery")
    status = Column(String, nullable=False, default="pending")
    timeline = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)

def init_db():
    Base.metadata.create_all(bind=engine)
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

def model_to_dict(obj):
    if obj is None:
        return None
    d = {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
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

def models_to_list(rows):
    return [model_to_dict(r) for r in rows]

def public_user(user):
    data = {
        "id": user.get("id"),
        "name": user.get("name"),
        "email": user.get("email") or "",
        "phone": user.get("phone") or "",
        "is_admin": bool(user.get("is_admin")),
    }
    if data["email"].startswith("phone_") and data["email"].endswith("@mobile.local"):
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

def get_user_by_id(db: Session, user_id: str):
    user = db.query(User).filter(User.id == user_id).first()
    return model_to_dict(user)

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer), db: Session = Depends(get_db)):
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
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

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
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

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
    if not user_dict or not user_dict.get("password") or not verify_password(body.password, user_dict["password"]):
        raise HTTPException(401, "Invalid email/mobile number or password")
    token = create_token({"sub": user_dict["id"]})
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
    return {"ok": True, "message": "OTP sent", "dev_otp": otp, "expires_in_minutes": OTP_EXPIRE_MINUTES}

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
def me(user=Depends(get_current_user)):
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
        stock=p["stock"],
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
    product.stock = p["stock"]
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

# ── Orders ────────────────────────────────────────────────────────
ORDER_STATUSES = ["pending", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled"]

@app.post("/api/orders")
def create_order(body: OrderIn, user=Depends(get_current_user), db: Session = Depends(get_db)):
    items_detail = []
    subtotal = 0
    for ci in body.items:
        product = db.query(Product).filter(Product.id == ci.product_id).first()
        p = model_to_dict(product)
        if not p:
            raise HTTPException(400, f"Product {ci.product_id} not found")
        if not p.get("available"):
            raise HTTPException(400, f"{p['name']} is unavailable")
        if ci.selected_weight not in p.get("quantity_options", [100, 250, 500, 1000]):
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
        status="pending",
        timeline=json.dumps(timeline),
        created_at=now_iso(),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    result = model_to_dict(order)
    result["delivery_directions_url"] = make_directions_url(body.delivery_lat, body.delivery_lng, body.address)
    return result

@app.get("/api/orders")
def list_orders(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if user.get("is_admin"):
        rows = db.query(Order).order_by(Order.created_at.desc()).all()
    else:
        rows = db.query(Order).filter(Order.user_id == user["id"]).order_by(Order.created_at.desc()).all()
    return models_to_list(rows)

@app.get("/api/orders/{oid}")
def get_order(oid: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == oid).first()
    order_dict = model_to_dict(order)
    if not order_dict:
        raise HTTPException(404, "Order not found")
    if not user.get("is_admin") and order_dict["user_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    return order_dict

@app.put("/api/orders/{oid}/status", dependencies=[Depends(require_admin)])
def update_order_status(oid: str, body: OrderStatusIn, db: Session = Depends(get_db)):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {ORDER_STATUSES}")
    order = db.query(Order).filter(Order.id == oid).first()
    if not order:
        raise HTTPException(404, "Order not found")
    order_dict = model_to_dict(order)
    timeline = order_dict.get("timeline", [])
    timeline.append({"status": body.status, "at": now_iso()})
    order.status = body.status
    order.timeline = json.dumps(timeline)
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
    db_type = "postgresql" if DATABASE_URL.startswith("postgresql") else "sqlite"
    return {"status": "ok", "service": "Amar Veggies SQLAlchemy API", "database": db_type, "shop_location_configured": bool(SHOP_LAT and SHOP_LNG)}

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

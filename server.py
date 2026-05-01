"""
HariyaliVeg - Backend API
Run: pip install fastapi uvicorn pymongo python-jose[cryptography] passlib[bcrypt] python-multipart
Then: python server.py
"""

from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from pymongo import MongoClient
import uuid, os
import certifi

# ── Config ────────────────────────────────────────────────────────
SECRET_KEY = os.getenv("SECRET_KEY", "hariyaliveg-secret-change-in-prod")
ALGORITHM  = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
DB_NAME   = "hariyaliveg"

# ── App ───────────────────────────────────────────────────────────
app = FastAPI(title="HariyaliVeg API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DB ────────────────────────────────────────────────────────────
mongo_kwargs = {}

if MONGO_URI.startswith("mongodb+srv://"):
    mongo_kwargs["tls"] = True
    mongo_kwargs["tlsCAFile"] = certifi.where()

client = None

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000, **mongo_kwargs)
    client.server_info()
    print("✅ MongoDB connected")
except Exception as e:
    print("❌ MongoDB connection failed:", e)

if client is None:
    raise RuntimeError("MongoDB connection failed. Check MONGO_URI.")

db = client[DB_NAME]
users_col    = db["users"]
products_col = db["products"]
orders_col   = db["orders"]

# ── Security ──────────────────────────────────────────────────────
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer  = HTTPBearer(auto_error=False)

def hash_password(p):   return pwd_ctx.hash(p)
def verify_password(p, h): return pwd_ctx.verify(p, h)

def create_token(data: dict):
    exp = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({**data, "exp": exp}, SECRET_KEY, algorithm=ALGORITHM)

def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None

def clean(doc):
    """Remove MongoDB _id and return clean dict."""
    if doc is None: return None
    doc.pop("_id", None)
    return doc

def clean_list(docs): return [clean(d) for d in docs]

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds:
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = decode_token(creds.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = users_col.find_one({"id": payload.get("sub")})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return clean(user)

def get_optional_user(creds: HTTPAuthorizationCredentials = Depends(bearer)):
    if not creds: return None
    payload = decode_token(creds.credentials)
    if not payload: return None
    user = users_col.find_one({"id": payload.get("sub")})
    return clean(user) if user else None

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
    email: str
    password: str

class ProductIn(BaseModel):
    name: str
    description: Optional[str] = ""
    emoji: Optional[str] = "🌿"
    category: str
    price: float   # price per kg
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

class OrderStatusIn(BaseModel):
    status: str

# ── Seed Admin ────────────────────────────────────────────────────
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

def seed_admin():
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        print("⚠️ Admin env vars missing, skipping seed admin")
        return

    if not users_col.find_one({"email": ADMIN_EMAIL}):
        users_col.insert_one({
            "id": str(uuid.uuid4()),
            "name": "Admin",
            "email": ADMIN_EMAIL,
            "password": hash_password(ADMIN_PASSWORD),
            "is_admin": True,
            "created_at": datetime.utcnow().isoformat(),
        })
        print("✅ Seeded admin")

try:
    seed_admin()
    print("✅ Admin seeded / checked")
except Exception as e:
    print("❌ Seed admin failed:", e)

# ── Auth ──────────────────────────────────────────────────────────
@app.post("/api/auth/register")
def register(body: RegisterIn):
    if users_col.find_one({"email": body.email}):
        raise HTTPException(400, "Email already registered")
    user = {
        "id":       str(uuid.uuid4()),
        "name":     body.name,
        "email":    body.email,
        "password": hash_password(body.password),
        "is_admin": False,
        "created_at": datetime.utcnow().isoformat(),
    }
    users_col.insert_one(user)
    token = create_token({"sub": user["id"]})
    return {"token": token, "user": {k: user[k] for k in ("id","name","email","is_admin")}}

@app.post("/api/auth/login")
def login(body: LoginIn):
    user = users_col.find_one({"email": body.email})
    if not user or not verify_password(body.password, user["password"]):
        raise HTTPException(401, "Invalid email or password")
    token = create_token({"sub": user["id"]})
    return {"token": token, "user": {k: user[k] for k in ("id","name","email","is_admin")}}

@app.get("/api/auth/me")
def me(user=Depends(get_current_user)):
    return {k: user[k] for k in ("id","name","email","is_admin")}

# ── Products ──────────────────────────────────────────────────────
@app.get("/api/products")
def list_products(category: Optional[str] = None, search: Optional[str] = None,
                  featured: Optional[bool] = None):
    q = {}
    if category and category != "All": q["category"] = category
    if search: q["name"] = {"$regex": search, "$options": "i"}
    if featured is not None: q["featured"] = featured
    return clean_list(list(products_col.find(q)))

@app.get("/api/products/{pid}")
def get_product(pid: str):
    p = products_col.find_one({"id": pid})
    if not p: raise HTTPException(404, "Product not found")
    return clean(p)

@app.post("/api/products", dependencies=[Depends(require_admin)])
def create_product(body: ProductIn):
    p = body.dict()
    p["id"] = str(uuid.uuid4())
    p["created_at"] = datetime.utcnow().isoformat()
    products_col.insert_one(p)
    return clean(p)

@app.put("/api/products/{pid}", dependencies=[Depends(require_admin)])
def update_product(pid: str, body: ProductIn):
    p = products_col.find_one({"id": pid})
    if not p: raise HTTPException(404, "Product not found")
    products_col.update_one({"id": pid}, {"$set": body.dict()})
    return clean(products_col.find_one({"id": pid}))

@app.delete("/api/products/{pid}", dependencies=[Depends(require_admin)])
def delete_product(pid: str):
    r = products_col.delete_one({"id": pid})
    if r.deleted_count == 0: raise HTTPException(404, "Product not found")
    return {"ok": True}

# ── Orders ────────────────────────────────────────────────────────
ORDER_STATUSES = ["pending", "confirmed", "packed", "out_for_delivery", "delivered", "cancelled"]

@app.post("/api/orders")
def create_order(body: OrderIn, user=Depends(get_current_user)):
    items_detail = []
    subtotal = 0
    for ci in body.items:
        p = products_col.find_one({"id": ci.product_id})
        if not p: raise HTTPException(400, f"Product {ci.product_id} not found")
        if not p.get("available"): raise HTTPException(400, f"{p['name']} is unavailable")
        if ci.selected_weight not in p.get("quantity_options", [100, 250, 500, 1000]):
            raise HTTPException(400, f"Invalid weight option for {p['name']}")
        weight = ci.selected_weight or 1000
        weight_factor = weight / 1000
        line_total = round(p["price"] * weight_factor * ci.quantity, 2)
        subtotal += line_total
        items_detail.append({
            "product_id": ci.product_id,
            "name": p["name"], "emoji": p.get("emoji","🌿"),
            "price": p["price"], "unit": p["unit"],
            "quantity": ci.quantity, "line_total": line_total,
            "selected_weight": ci.selected_weight,
        })
    delivery = 0 if subtotal >= 300 else 40
    order = {
        "id":         str(uuid.uuid4()),
        "user_id":    user["id"],
        "user_name":  user["name"],
        "user_email": user["email"],
        "items":      items_detail,
        "address":    body.address,
        "phone":      body.phone,
        "slot":       body.slot,
        "notes":      body.notes,
        "subtotal":   round(subtotal, 2),
        "delivery":   delivery,
        "total":      round(subtotal + delivery, 2),
        "payment":    "Cash on Delivery",
        "status":     "pending",
        "timeline":   [{"status": "pending", "at": datetime.utcnow().isoformat()}],
        "created_at": datetime.utcnow().isoformat(),
    }
    orders_col.insert_one(order)
    return clean(order)

@app.get("/api/orders")
def list_orders(user=Depends(get_current_user)):
    if user.get("is_admin"):
        orders = list(orders_col.find().sort("created_at", -1))
    else:
        orders = list(orders_col.find({"user_id": user["id"]}).sort("created_at", -1))
    return clean_list(orders)

@app.get("/api/orders/{oid}")
def get_order(oid: str, user=Depends(get_current_user)):
    o = orders_col.find_one({"id": oid})
    if not o: raise HTTPException(404, "Order not found")
    if not user.get("is_admin") and o["user_id"] != user["id"]:
        raise HTTPException(403, "Access denied")
    return clean(o)

@app.put("/api/orders/{oid}/status", dependencies=[Depends(require_admin)])
def update_order_status(oid: str, body: OrderStatusIn):
    if body.status not in ORDER_STATUSES:
        raise HTTPException(400, f"Invalid status. Valid: {ORDER_STATUSES}")
    o = orders_col.find_one({"id": oid})
    if not o: raise HTTPException(404, "Order not found")
    timeline = o.get("timeline", [])
    timeline.append({"status": body.status, "at": datetime.utcnow().isoformat()})
    orders_col.update_one({"id": oid}, {"$set": {"status": body.status, "timeline": timeline}})
    return clean(orders_col.find_one({"id": oid}))

# ── Admin stats ───────────────────────────────────────────────────
@app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
def admin_stats():
    total_orders   = orders_col.count_documents({})
    pending_orders = orders_col.count_documents({"status": "pending"})
    total_products = products_col.count_documents({})
    avail_products = products_col.count_documents({"available": True})
    revenue_docs   = list(orders_col.find({"status": {"$ne": "cancelled"}}, {"total": 1}))
    revenue        = sum(d.get("total", 0) for d in revenue_docs)
    total_users    = users_col.count_documents({"is_admin": False})
    return {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "total_products": total_products,
        "available_products": avail_products,
        "revenue": round(revenue, 2),
        "total_users": total_users,
    }

# ── Health ────────────────────────────────────────────────────────
@app.get("/api/health")
def health(): return {"status": "ok", "service": "HariyaliVeg API"}

# ── Migration helpers ───────────────────────────────────────────────
def ensure_quantity_options():
    """Add default quantity options to products missing the field."""
    products_col.update_many(
        {"quantity_options": {"$exists": False}},
        {"$set": {"quantity_options": [100, 250, 500, 1000]}}
    )

@app.post("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
async def upload_product_image(pid: str, file: UploadFile = File(...)):
    p = products_col.find_one({"id": pid})
    if not p: raise HTTPException(404, "Product not found")
    if not file.content_type.startswith("image/"):
        raise HTTPException(400, "File must be an image (jpg, png, webp, etc.)")
    contents = await file.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 5MB")
    import base64
    b64 = base64.b64encode(contents).decode("utf-8")
    image_data = f"data:{file.content_type};base64,{b64}"
    products_col.update_one({"id": pid}, {"$set": {"image_data": image_data}})
    return {"ok": True, "image_data": image_data}

@app.delete("/api/products/{pid}/image", dependencies=[Depends(require_admin)])
def delete_product_image(pid: str):
    p = products_col.find_one({"id": pid})
    if not p: raise HTTPException(404, "Product not found")
    products_col.update_one({"id": pid}, {"$unset": {"image_data": ""}})
    return {"ok": True}

if __name__ == "__main__":
    import uvicorn, os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("server:app", host="0.0.0.0", port=port)
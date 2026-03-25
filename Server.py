from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, date, timedelta
from pymongo import MongoClient
from bson import ObjectId
import os
from dotenv import load_dotenv
from auth import (
    get_password_hash, verify_password, create_access_token,
    get_current_user, get_current_manager
)

load_dotenv()

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB connection
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "sales_tracker")
client = MongoClient(MONGO_URL)
db = client[DB_NAME]
users_collection = db.users
leads_collection = db.leads
followups_collection = db.followups
comments_collection = db.comments
activity_log_collection = db.activity_log
orders_collection = db.orders

# Pydantic models
class UserCreate(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    phone: Optional[str] = None

class UserLogin(BaseModel):
    username: str
    password: str

class Lead(BaseModel):
    name: str
    phone: str
    city: str
    product: str
    lead_source: str
    lead_status: str
    priority_level: str
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    next_followup_date: Optional[str] = None
    lead_stage: Optional[str] = None

class LeadUpdate(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    product: Optional[str] = None
    lead_source: Optional[str] = None
    lead_status: Optional[str] = None
    priority_level: Optional[str] = None
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    next_followup_date: Optional[str] = None
    lead_stage: Optional[str] = None

class LeadAssignment(BaseModel):
    lead_id: str
    assigned_to: str

class FollowUp(BaseModel):
    lead_id: str
    followup_date: str
    notes: Optional[str] = None

class FollowUpUpdate(BaseModel):
    status: str
    completed_at: Optional[str] = None

class Comment(BaseModel):
    lead_id: str
    comment_text: str

class Order(BaseModel):
    lead_id: str
    order_value: float
    product_type: str
    quotation_amount: float
    deal_amount: float
    notes: Optional[str] = None

# Helper functions
def serialize_doc(doc):
    if doc:
        doc["_id"] = str(doc["_id"])
        if "created_at" in doc and isinstance(doc["created_at"], datetime):
            doc["created_at"] = doc["created_at"].isoformat()
        if "updated_at" in doc and isinstance(doc["updated_at"], datetime):
            doc["updated_at"] = doc["updated_at"].isoformat()
        if "completed_at" in doc and doc["completed_at"] and isinstance(doc["completed_at"], datetime):
            doc["completed_at"] = doc["completed_at"].isoformat()
        if "password" in doc:
            del doc["password"]
    return doc

def log_activity(user_id: str, action: str, entity_type: str, entity_id: str, details: dict = None):
    activity = {
        "user_id": user_id,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "details": details or {},
        "timestamp": datetime.utcnow()
    }
    activity_log_collection.insert_one(activity)

def init_default_users():
    if users_collection.count_documents({}) == 0:
        admin_user = {
            "username": "admin",
            "password": get_password_hash("admin123"),
            "full_name": "Admin User",
            "role": "manager",
            "phone": None,
            "created_at": datetime.utcnow(),
            "is_active": True
        }
        users_collection.insert_one(admin_user)
        print("Default admin user created")

init_default_users()

@app.get("/")
def read_root():
    return {"message": "Sales CRM API"}

@app.get("/api")
def api_root():
    return {"message": "Sales CRM API", "status": "running"}
  # ==================== AUTHENTICATION ENDPOINTS ====================

@app.post("/api/auth/login")
def login(user_login: UserLogin):
    user = users_collection.find_one({"username": user_login.username})
    if not user or not verify_password(user_login.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    if not user.get("is_active", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated"
        )
    
    access_token = create_access_token(
        data={
            "sub": str(user["_id"]),
            "username": user["username"],
            "role": user["role"]
        }
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": serialize_doc(user)
    }

@app.post("/api/auth/register")
def register(user_create: UserCreate, current_user: dict = Depends(get_current_manager)):
    if users_collection.find_one({"username": user_create.username}):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    user_dict = user_create.dict()
    user_dict["password"] = get_password_hash(user_create.password)
    user_dict["created_at"] = datetime.utcnow()
    user_dict["is_active"] = True
    user_dict["created_by"] = current_user["user_id"]
    
    result = users_collection.insert_one(user_dict)
    user_dict["_id"] = str(result.inserted_id)
    
    log_activity(current_user["user_id"], "created", "user", str(result.inserted_id),
                 {"username": user_create.username})
    
    del user_dict["password"]
    return user_dict

@app.get("/api/users")
def get_users(current_user: dict = Depends(get_current_user)):
    users = list(users_collection.find({"role": "sales_person", "is_active": True}))
    return [serialize_doc(user) for user in users]

@app.get("/api/users/all")
def get_all_users(current_user: dict = Depends(get_current_manager)):
    users = list(users_collection.find({}))
    return [serialize_doc(user) for user in users]

# ==================== LEAD ENDPOINTS ====================

@app.post("/api/leads")
def create_lead(lead: Lead, current_user: dict = Depends(get_current_user)):
    lead_dict = lead.dict()
    lead_dict["created_at"] = datetime.utcnow()
    lead_dict["updated_at"] = datetime.utcnow()
    lead_dict["created_by"] = current_user["user_id"]
    
    if current_user["role"] == "sales_person":
        lead_dict["assigned_to"] = current_user["user_id"]
        lead_dict["assigned_date"] = datetime.utcnow().isoformat()
    
    result = leads_collection.insert_one(lead_dict)
    lead_dict["_id"] = str(result.inserted_id)
    
    log_activity(current_user["user_id"], "created", "lead", str(result.inserted_id),
                 {"lead_name": lead.name, "lead_status": lead.lead_status})
    
    return lead_dict

@app.get("/api/leads")
def get_leads(current_user: dict = Depends(get_current_user)):
    if current_user["role"] in ["manager", "admin"]:
        leads = list(leads_collection.find({}))
    else:
        leads = list(leads_collection.find({"assigned_to": current_user["user_id"]}))
    
    for lead in leads:
        lead = serialize_doc(lead)
        if lead.get("assigned_to"):
            assigned_user = users_collection.find_one({"_id": ObjectId(lead["assigned_to"])})
            if assigned_user:
                lead["assigned_user"] = {
                    "id": str(assigned_user["_id"]),
                    "name": assigned_user.get("full_name", assigned_user["username"]),
                    "username": assigned_user["username"]
                }
    
    return [serialize_doc(lead) for lead in leads]

@app.get("/api/leads/{lead_id}")
def get_lead(lead_id: str, current_user: dict = Depends(get_current_user)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    if current_user["role"] == "sales_person" and lead.get("assigned_to") != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    lead = serialize_doc(lead)
    if lead.get("assigned_to"):
        assigned_user = users_collection.find_one({"_id": ObjectId(lead["assigned_to"])})
        if assigned_user:
            lead["assigned_user"] = {
                "id": str(assigned_user["_id"]),
                "name": assigned_user.get("full_name", assigned_user["username"]),
                "username": assigned_user["username"]
            }
    
    return lead

@app.put("/api/leads/{lead_id}")
def update_lead(lead_id: str, lead_update: LeadUpdate, current_user: dict = Depends(get_current_user)):
    try:
        existing_lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not existing_lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    if current_user["role"] == "sales_person" and existing_lead.get("assigned_to") != current_user["user_id"]:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_data = {k: v for k, v in lead_update.dict().items() if v is not None}
    update_data["updated_at"] = datetime.utcnow()
    
    leads_collection.update_one({"_id": ObjectId(lead_id)}, {"$set": update_data})
    
    log_activity(current_user["user_id"], "updated", "lead", lead_id, update_data)
    
    updated_lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    return serialize_doc(updated_lead)

@app.delete("/api/leads/{lead_id}")
def delete_lead(lead_id: str, current_user: dict = Depends(get_current_user)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    leads_collection.delete_one({"_id": ObjectId(lead_id)})
    followups_collection.delete_many({"lead_id": lead_id})
    comments_collection.delete_many({"lead_id": lead_id})
    
    log_activity(current_user["user_id"], "deleted", "lead", lead_id)
    
    return {"message": "Lead deleted successfully"}

@app.post("/api/leads/assign")
def assign_lead(assignment: LeadAssignment, current_user: dict = Depends(get_current_manager)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(assignment.lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    try:
        user = users_collection.find_one({"_id": ObjectId(assignment.assigned_to)})
    except:
        raise HTTPException(status_code=404, detail="Invalid user ID")
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    leads_collection.update_one(
        {"_id": ObjectId(assignment.lead_id)},
        {"$set": {
            "assigned_to": assignment.assigned_to,
            "assigned_date": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow()
        }}
    )
    
    log_activity(current_user["user_id"], "assigned", "lead", assignment.lead_id,
                 {"assigned_to": assignment.assigned_to, "assigned_to_name": user.get("full_name")})
    
    return {"message": "Lead assigned successfully"}
  # ==================== FOLLOWUP ENDPOINTS ====================

@app.post("/api/followups")
def create_followup(followup: FollowUp, current_user: dict = Depends(get_current_user)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(followup.lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    followup_dict = followup.dict()
    followup_dict["created_at"] = datetime.utcnow()
    followup_dict["created_by"] = current_user["user_id"]
    followup_dict["status"] = "pending"
    
    result = followups_collection.insert_one(followup_dict)
    followup_dict["_id"] = str(result.inserted_id)
    
    leads_collection.update_one(
        {"_id": ObjectId(followup.lead_id)},
        {"$set": {"next_followup_date": followup.followup_date, "updated_at": datetime.utcnow()}}
    )
    
    log_activity(current_user["user_id"], "created", "followup", str(result.inserted_id),
                 {"lead_id": followup.lead_id, "followup_date": followup.followup_date})
    
    return followup_dict

@app.get("/api/leads/{lead_id}/followups")
def get_lead_followups(lead_id: str, current_user: dict = Depends(get_current_user)):
    followups = list(followups_collection.find({"lead_id": lead_id}).sort("followup_date", -1))
    return [serialize_doc(f) for f in followups]

@app.put("/api/followups/{followup_id}")
def update_followup(followup_id: str, followup_update: FollowUpUpdate, current_user: dict = Depends(get_current_user)):
    try:
        followup = followups_collection.find_one({"_id": ObjectId(followup_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid followup ID")
    
    if not followup:
        raise HTTPException(status_code=404, detail="Followup not found")
    
    update_data = followup_update.dict()
    if update_data.get("completed_at"):
        update_data["completed_at"] = datetime.fromisoformat(update_data["completed_at"].replace("Z", "+00:00"))
    
    followups_collection.update_one({"_id": ObjectId(followup_id)}, {"$set": update_data})
    
    log_activity(current_user["user_id"], "updated", "followup", followup_id, update_data)
    
    updated_followup = followups_collection.find_one({"_id": ObjectId(followup_id)})
    return serialize_doc(updated_followup)

@app.get("/api/followups/today")
def get_today_followups(current_user: dict = Depends(get_current_user)):
    today = date.today().isoformat()
    
    if current_user["role"] in ["manager", "admin"]:
        followups = list(followups_collection.find({
            "followup_date": today,
            "status": "pending"
        }))
    else:
        lead_ids = [str(l["_id"]) for l in leads_collection.find({"assigned_to": current_user["user_id"]})]
        followups = list(followups_collection.find({
            "lead_id": {"$in": lead_ids},
            "followup_date": today,
            "status": "pending"
        }))
    
    for followup in followups:
        followup = serialize_doc(followup)
        lead = leads_collection.find_one({"_id": ObjectId(followup["lead_id"])})
        if lead:
            followup["lead_info"] = {
                "name": lead.get("name"),
                "phone": lead.get("phone"),
                "lead_status": lead.get("lead_status"),
                "lead_stage": lead.get("lead_stage")
            }
    
    return [serialize_doc(f) for f in followups]

@app.get("/api/followups/overdue")
def get_overdue_followups(current_user: dict = Depends(get_current_user)):
    today = date.today().isoformat()
    
    if current_user["role"] in ["manager", "admin"]:
        followups = list(followups_collection.find({
            "followup_date": {"$lt": today},
            "status": "pending"
        }))
    else:
        lead_ids = [str(l["_id"]) for l in leads_collection.find({"assigned_to": current_user["user_id"]})]
        followups = list(followups_collection.find({
            "lead_id": {"$in": lead_ids},
            "followup_date": {"$lt": today},
            "status": "pending"
        }))
    
    for followup in followups:
        followup = serialize_doc(followup)
        lead = leads_collection.find_one({"_id": ObjectId(followup["lead_id"])})
        if lead:
            followup["lead_info"] = {
                "name": lead.get("name"),
                "phone": lead.get("phone"),
                "lead_status": lead.get("lead_status"),
                "lead_stage": lead.get("lead_stage")
            }
    
    return [serialize_doc(f) for f in followups]

# ==================== COMMENT ENDPOINTS ====================

@app.post("/api/comments")
def create_comment(comment: Comment, current_user: dict = Depends(get_current_user)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(comment.lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    user = users_collection.find_one({"_id": ObjectId(current_user["user_id"])})
    
    comment_dict = comment.dict()
    comment_dict["created_at"] = datetime.utcnow()
    comment_dict["created_by"] = current_user["user_id"]
    comment_dict["created_by_name"] = user.get("full_name", current_user["username"]) if user else current_user["username"]
    
    result = comments_collection.insert_one(comment_dict)
    comment_dict["_id"] = str(result.inserted_id)
    
    log_activity(current_user["user_id"], "created", "comment", str(result.inserted_id),
                 {"lead_id": comment.lead_id})
    
    return comment_dict

@app.get("/api/leads/{lead_id}/comments")
def get_lead_comments(lead_id: str, current_user: dict = Depends(get_current_user)):
    comments = list(comments_collection.find({"lead_id": lead_id}).sort("created_at", -1))
    return [serialize_doc(c) for c in comments]

# ==================== ORDER ENDPOINTS ====================

@app.post("/api/orders")
def create_order(order: Order, current_user: dict = Depends(get_current_user)):
    try:
        lead = leads_collection.find_one({"_id": ObjectId(order.lead_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid lead ID")
    
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    
    existing_order = orders_collection.find_one({"lead_id": order.lead_id})
    if existing_order:
        raise HTTPException(status_code=400, detail="Order already exists for this lead")
    
    order_dict = order.dict()
    order_dict["order_date"] = datetime.utcnow().isoformat()
    order_dict["created_at"] = datetime.utcnow()
    order_dict["created_by"] = current_user["user_id"]
    
    result = orders_collection.insert_one(order_dict)
    order_dict["_id"] = str(result.inserted_id)
    
    leads_collection.update_one(
        {"_id": ObjectId(order.lead_id)},
        {"$set": {"lead_status": "deal_closed", "updated_at": datetime.utcnow()}}
    )
    
    log_activity(current_user["user_id"], "created", "order", str(result.inserted_id),
                 {"lead_id": order.lead_id, "deal_amount": order.deal_amount})
    
    return order_dict

@app.get("/api/leads/{lead_id}/order")
def get_lead_order(lead_id: str, current_user: dict = Depends(get_current_user)):
    order = orders_collection.find_one({"lead_id": lead_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return serialize_doc(order)

@app.get("/api/orders")
def get_all_orders(current_user: dict = Depends(get_current_manager)):
    orders = list(orders_collection.find({}).sort("created_at", -1))
    return [serialize_doc(o) for o in orders]
# ==================== MANAGER STATS ENDPOINTS ====================

@app.get("/api/manager/stats")
def get_manager_stats(current_user: dict = Depends(get_current_user)):
    total_leads = leads_collection.count_documents({})
    
    # Count by status
    statuses = {}
    for status in ["contacted", "site_visit_done", "quotation_sent", "negotiation", "deal_closed", "lost"]:
        statuses[status] = leads_collection.count_documents({"lead_status": status})
    
    # Unassigned leads
    unassigned_leads = leads_collection.count_documents({"assigned_to": None})
    
    # Today's followups
    today = date.today().isoformat()
    if current_user["role"] in ["manager", "admin"]:
        today_followups = followups_collection.count_documents({
            "followup_date": today,
            "status": "pending"
        })
        overdue_followups = followups_collection.count_documents({
            "followup_date": {"$lt": today},
            "status": "pending"
        })
    else:
        lead_ids = [str(l["_id"]) for l in leads_collection.find({"assigned_to": current_user["user_id"]})]
        today_followups = followups_collection.count_documents({
            "lead_id": {"$in": lead_ids},
            "followup_date": today,
            "status": "pending"
        })
        overdue_followups = followups_collection.count_documents({
            "lead_id": {"$in": lead_ids},
            "followup_date": {"$lt": today},
            "status": "pending"
        })
    
    # Active sales persons
    active_sales_persons = users_collection.count_documents({"role": "sales_person", "is_active": True})
    
    # Monthly sales calculation
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_orders = list(orders_collection.find({
        "created_at": {"$gte": first_day_of_month}
    }))
    monthly_sales = sum(order.get("deal_amount", 0) for order in monthly_orders)
    monthly_order_count = len(monthly_orders)
    
    # Conversion ratio (deals closed / total leads * 100)
    closed_deals = statuses.get("deal_closed", 0)
    conversion_ratio = round((closed_deals / total_leads * 100), 1) if total_leads > 0 else 0
    
    return {
        "total_leads": total_leads,
        "unassigned_leads": unassigned_leads,
        "statuses": statuses,
        "today_followups": today_followups,
        "overdue_followups": overdue_followups,
        "active_sales_persons": active_sales_persons,
        "monthly_sales": monthly_sales,
        "monthly_orders": monthly_order_count,
        "conversion_ratio": conversion_ratio,
        "closed_deals": closed_deals
    }

# ==================== ACTIVITY LOG ENDPOINTS ====================

@app.get("/api/activity-log")
def get_activity_log(current_user: dict = Depends(get_current_manager), limit: int = 50):
    activities = list(activity_log_collection.find({}).sort("timestamp", -1).limit(limit))
    
    for activity in activities:
        activity["_id"] = str(activity["_id"])
        if "timestamp" in activity and isinstance(activity["timestamp"], datetime):
            activity["timestamp"] = activity["timestamp"].isoformat()
        
        user = users_collection.find_one({"_id": ObjectId(activity["user_id"])})
        if user:
            activity["user_name"] = user.get("full_name", user["username"])
    
    return activities

# ==================== SALES PERSON STATS ====================

@app.get("/api/sales-person/stats/{user_id}")
def get_sales_person_stats(user_id: str, current_user: dict = Depends(get_current_manager)):
    try:
        user = users_collection.find_one({"_id": ObjectId(user_id)})
    except:
        raise HTTPException(status_code=404, detail="Invalid user ID")
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    total_leads = leads_collection.count_documents({"assigned_to": user_id})
    
    statuses = {}
    for status in ["contacted", "site_visit_done", "quotation_sent", "negotiation", "deal_closed", "lost"]:
        statuses[status] = leads_collection.count_documents({"assigned_to": user_id, "lead_status": status})
    
    today = date.today().isoformat()
    lead_ids = [str(l["_id"]) for l in leads_collection.find({"assigned_to": user_id})]
    
    today_followups = followups_collection.count_documents({
        "lead_id": {"$in": lead_ids},
        "followup_date": today,
        "status": "pending"
    })
    
    overdue_followups = followups_collection.count_documents({
        "lead_id": {"$in": lead_ids},
        "followup_date": {"$lt": today},
        "status": "pending"
    })
    
    first_day_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_orders = list(orders_collection.find({
        "lead_id": {"$in": lead_ids},
        "created_at": {"$gte": first_day_of_month}
    }))
    monthly_sales = sum(order.get("deal_amount", 0) for order in monthly_orders)
    
    closed_deals = statuses.get("deal_closed", 0)
    conversion_ratio = round((closed_deals / total_leads * 100), 1) if total_leads > 0 else 0
    
    return {
        "user_id": user_id,
        "user_name": user.get("full_name", user["username"]),
        "total_leads": total_leads,
        "statuses": statuses,
        "today_followups": today_followups,
        "overdue_followups": overdue_followups,
        "monthly_sales": monthly_sales,
        "conversion_ratio": conversion_ratio,
        "closed_deals": closed_deals
    }

# ==================== HEALTH CHECK ====================

@app.get("/api/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)






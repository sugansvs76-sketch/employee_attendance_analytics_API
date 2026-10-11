# uvicorn app.main:app --port 8000
import os
import re
import calendar
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError
from bson import json_util
from bson.json_util import CANONICAL_JSON_OPTIONS

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "attendance_db")

client = MongoClient(MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
db = client[MONGO_DB]

employees = db["employees"]
attendance_logs = db["attendance_logs"]

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

PRESENCE = {"PRESENT", "WFH", "ON_DUTY"}
STATUSES = PRESENCE | {"ABSENT", "LEAVE"}

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# ------------------------------ Helpers -------------------------------------

def fail(code: int, message: str):
    raise HTTPException(status_code=code, detail=message)


def now_utc():
    return datetime.now(UTC).replace(microsecond=0)


def truncate_second(value: datetime) -> datetime:
    return value.replace(microsecond=0)


def epoch_ms(value: Optional[datetime]):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp() * 1000)


def parse_epoch(value) -> datetime:
    if type(value) is not int or not 100000000000 <= value <= 4102444800000:
        fail(422, "Timestamp must be an integer epoch-millisecond value")
    return truncate_second(datetime.fromtimestamp(value / 1000, UTC))


def validate_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        fail(422, "Date must use YYYY-MM-DD format")


def validate_month(value: str) -> tuple[date, date]:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value or ""):
        fail(422, "Month must use YYYY-MM format")
    year, month = map(int, value.split("-"))
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def working_days(start: date, end: date) -> int:
    if end < start:
        return 0
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def round_half_up(value, places=2):
    if value is None:
        return None
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(
        quantum, rounding=ROUND_HALF_UP
    ))


def as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(IST)


def shift_start_instant(punch: datetime, shift_start: str) -> datetime:
    local = as_ist(punch)
    hour, minute = map(int, shift_start.split(":"))
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def shift_end_instant(attendance_date: str, shift_start: str,
                      shift_end: str) -> datetime:
    day = date.fromisoformat(attendance_date)
    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))
    end_day = day + timedelta(days=1) if shift_end <= shift_start else day
    return datetime.combine(
        end_day, time(end_hour, end_minute), tzinfo=IST
    )


def attendance_date_for(punch: datetime, shift_start: str,
                        shift_end: str) -> str:
    local = as_ist(punch)
    result = local.date()
    if shift_end <= shift_start:
        end_hour, end_minute = map(int, shift_end.split(":"))
        if (local.hour, local.minute) < (end_hour, end_minute):
            result -= timedelta(days=1)
    return result.isoformat()


def late_minutes(punch_in: datetime, shift_start: str) -> int:
    local = as_ist(punch_in)
    start = shift_start_instant(local, shift_start)
    elapsed = (local - start).total_seconds()
    if elapsed <= 600:
        return 0
    return max(0, int(elapsed // 60))


def derived_values(punch_in, punch_out, shift_start, shift_end, day):
    if punch_in is None:
        return {
            "work_hours": None,
            "late_minutes": 0,
            "overtime_minutes": 0,
            "half_day": False,
        }

    late = late_minutes(punch_in, shift_start)
    if punch_out is None:
        return {
            "work_hours": None,
            "late_minutes": late,
            "overtime_minutes": 0,
            "half_day": False,
        }

    seconds = (punch_out - punch_in).total_seconds()
    hours = round_half_up(seconds / 3600, 2)
    shift_end = shift_end_instant(day, shift_start, shift_end)
    extra = int((as_ist(punch_out) - shift_end).total_seconds() // 60)
    overtime = extra if extra >= 30 else 0

    return {
        "work_hours": hours,
        "late_minutes": late,
        "overtime_minutes": overtime,
        "half_day": hours < 4.50,
    }


def record_out(doc: dict) -> dict:
    """Convert stored BSON datetimes to API epoch milliseconds."""
    result = dict(doc)
    result.pop("_id", None)
    result["punch_in"] = epoch_ms(result.get("punch_in"))
    result["punch_out"] = epoch_ms(result.get("punch_out"))
    result["history"] = result.get("history") or []

    for entry in result["history"]:
        entry["at"] = epoch_ms(entry.get("at"))
        changes = entry.get("changes") or {}
        for field in ("punch_in", "punch_out"):
            if field in changes:
                changes[field]["from"] = epoch_ms(changes[field].get("from"))
                changes[field]["to"] = epoch_ms(changes[field].get("to"))
        entry["changes"] = changes

    result.setdefault("half_day", False)
    result.setdefault("late_minutes", 0)
    result.setdefault("overtime_minutes", 0)
    result.setdefault("work_hours", None)
    return result


def employee_out(doc: dict) -> dict:
    result = dict(doc)
    result.pop("_id", None)
    result["created_at"] = epoch_ms(result["created_at"])
    return result


def get_employee(emp_code: str):
    employee = employees.find_one({"emp_code": emp_code})
    if not employee:
        fail(404, "Employee not found")
    return employee



def ensure_indexes():
    employees.create_index(
        [("emp_code", ASCENDING)],
        unique=True,
        name="uq_employee_code"
    )

    employees.create_index(
        [("email", ASCENDING)],
        unique=True,
        name="uq_employee_email"
    )

    employees.create_index(
        [("department", ASCENDING), ("emp_code", ASCENDING)],
        name="ix_employee_department"
    )

    employees.create_index(
        [("joined_on", ASCENDING), ("department", ASCENDING)],
        name="ix_employee_joined_department"
    )

    attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)],
        unique=True,
        name="uq_attendance_employee_date"
    )

    attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)],
        name="ix_attendance_date_employee"
    )

    attendance_logs.create_index(
        [("status", ASCENDING), ("date", DESCENDING)],
        name="ix_attendance_status_date"
    )

    attendance_logs.create_index(
        [("date", ASCENDING), ("late_minutes", DESCENDING)],
        name="ix_attendance_date_late"
    )


@app.on_event("startup")
def startup():
    ensure_indexes()

# ------------------------------ Request models ------------------------------

class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = Field(default="09:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    shift_end: str = Field(default="18:30", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def check_joined_on(cls, value):
        validate_date(value)
        return value

    @field_validator("shift_end")
    @classmethod
    def check_shift_end(cls, value, info):
        start = info.data.get("shift_start", "09:30")
        if value == start:
            raise ValueError("shift_start and shift_end must differ")
        return value


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None:
            parse_epoch(value)
        return value

    @field_validator("status")
    @classmethod
    def check_status(cls, value):
        if value not in PRESENCE:
            raise ValueError("status must be PRESENT, WFH or ON_DUTY")
        return value


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None:
            parse_epoch(value)
        return value


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status")
    @classmethod
    def check_status(cls, value):
        if value is not None and value not in STATUSES:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("punch_in", "punch_out")
    @classmethod
    def check_timestamp(cls, value):
        if value is not None:
            parse_epoch(value)
        return value


# -------------------------------- Health -------------------------------------

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        fail(503, "MongoDB unavailable")


# ------------------------------- Employees ----------------------------------

@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if body.shift_start == body.shift_end:
        fail(422, "shift_start and shift_end must differ")

    doc = body.model_dump()
    doc["created_at"] = now_utc()

    
    try:
        employees.insert_one(doc)
    except DuplicateKeyError as exc:
        if "email" in str(exc):
            fail(409, "email already exists")
        fail(409, "emp_code already exists")


    return employee_out(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {"department": department} if department is not None else {}
    total = employees.count_documents(query)
    docs = list(
        employees.find(query, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [{**d, "created_at": epoch_ms(d["created_at"])} for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ------------------------------- Punch in -----------------------------------

@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = get_employee(body.emp_code)
    punched = parse_epoch(body.punched_at) if body.punched_at is not None else now_utc()
    day = attendance_date_for(punched, emp["shift_start"], emp["shift_end"])
    derived = derived_values(punched, None, emp["shift_start"], emp["shift_end"], day)

    doc = {
        "emp_code": body.emp_code,
        "date": day,
        "status": body.status,
        "punch_in": punched,
        "punch_out": None,
        **derived,
        "history": [],
    }

    try:
        attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        fail(409, "Already punched in for this attendance date")

    return record_out(doc)


# ------------------------------- Punch out ----------------------------------

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = get_employee(body.emp_code)
    punched = parse_epoch(body.punched_at) if body.punched_at is not None else now_utc()

    doc = attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": punched},
            "punch_out": None,
            "status": {"$in": list(PRESENCE)},
        },
        sort=[("punch_in", DESCENDING)],
    )
    if not doc:
        # Check whether there was a suitable record that is already closed.
        closed = attendance_logs.find_one(
            {"emp_code": body.emp_code, "punch_in": {"$lte": punched}},
            sort=[("punch_in", DESCENDING)],
        )
        if closed:
            fail(409, "Attendance record is already punched out")
        fail(404, "No open punch-in record found")

    if punched <= doc["punch_in"] or punched > doc["punch_in"] + timedelta(hours=24):
        fail(422, "Punch-out must be after punch-in and within 24 hours")

    values = derived_values(
        doc["punch_in"], punched, emp["shift_start"], emp["shift_end"], doc["date"]
    )
    result = attendance_logs.update_one(
        {"_id": doc["_id"], "punch_out": None},
        {"$set": {"punch_out": punched, **values}},
    )
    if result.modified_count != 1:
        fail(409, "Attendance record was punched out by another request")

    doc.update({"punch_out": punched, **values})
    return record_out(doc)


# ---------------------------- Attendance listing ----------------------------

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {}
    if emp_code:
        query["emp_code"] = emp_code
    if date_from:
        validate_date(date_from)
    if date_to:
        validate_date(date_to)
    if date_from and date_to and date_from > date_to:
        fail(422, "date_from must be on or before date_to")
    if date_from or date_to:
        query["date"] = {}
        if date_from:
            query["date"]["$gte"] = date_from
        if date_to:
            query["date"]["$lte"] = date_to
    if status:
        if status not in STATUSES:
            fail(422, "Invalid status")
        query["status"] = status

    total = attendance_logs.count_documents(query)
    docs = list(
        attendance_logs.find(query)
        .sort([("date", DESCENDING), ("emp_code", ASCENDING)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [record_out(d) for d in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ----------------------------- Regularization -------------------------------


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    validate_date(date)
    emp = get_employee(emp_code)
    doc = attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not doc:
        fail(404, "Attendance record not found")


    old = {
        "status": doc.get("status"),
        "punch_in": doc.get("punch_in"),
        "punch_out": doc.get("punch_out"),
        "work_hours": doc.get("work_hours"),
        "late_minutes": doc.get("late_minutes", 0),
        "overtime_minutes": doc.get("overtime_minutes", 0),
        "half_day": doc.get("half_day", False),
    }
    new = dict(old)

    supplied = body.model_dump(exclude_unset=True)
    for field in ("status", "punch_in", "punch_out"):
        if field in supplied and supplied[field] is not None:
            new[field] = parse_epoch(supplied[field]) if field != "status" else supplied[field]

    status = new["status"]
    if status in {"ABSENT", "LEAVE"}:
        if "punch_in" in supplied or "punch_out" in supplied:
            fail(422, "ABSENT and LEAVE cannot include punch times")
        new["punch_in"] = None
        new["punch_out"] = None
    elif new["punch_in"] is None:
        fail(422, "Presence status requires punch_in")

    if new["punch_in"] is not None:
        computed_day = attendance_date_for(
            new["punch_in"], emp["shift_start"], emp["shift_end"]
        )
        if computed_day != date:
            fail(422, "punch_in must belong to the attendance date")

    if new["punch_out"] is not None:
        if new["punch_in"] is None or new["punch_out"] <= new["punch_in"]:
            fail(422, "punch_out must be after punch_in")
        if new["punch_out"] > new["punch_in"] + timedelta(hours=24):
            fail(422, "punch_out must be within 24 hours")

    derived = derived_values(
    new["punch_in"], new["punch_out"],
    emp["shift_start"], emp["shift_end"], date
)
    new.update(derived)

    changes = {}
    for field in ("status", "punch_in", "punch_out", "work_hours",
                  "late_minutes", "overtime_minutes", "half_day"):
        before, after = old[field], new[field]
        if before != after:
            changes[field] = {
                "from": epoch_ms(before) if field in {"punch_in", "punch_out"} else before,
                "to": epoch_ms(after) if field in {"punch_in", "punch_out"} else after,
            }

    if not changes:
        fail(422, "Request does not change the record")

    history_entry = {
        "at": now_utc(),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    # Compare-and-set prevents concurrent edits from overwriting history.
    result = attendance_logs.update_one(
        {"_id": doc["_id"], "history": doc.get("history", [])},
        {
            "$set": {
                "status": new["status"],
                "punch_in": new["punch_in"],
                "punch_out": new["punch_out"],
                **derived,
            },
            "$push": {"history": history_entry},
        },
    )
    if result.modified_count != 1:
        fail(409, "Record changed concurrently; retry the correction")

    doc.update(new)
    doc.setdefault("history", []).append(history_entry)
    return record_out(doc)


# ---------------------------- Analytics helpers -----------------------------

def month_filter(month: str):
    start, end = validate_month(month)
    return start.isoformat(), end.isoformat()


def present_expression():
    return {"$in": ["$status", list(PRESENCE)]}


def present_day_expression():
    return {
        "$cond": [
            {
                "$and": [
                    present_expression(),
                    {"$lt": [{"$dayOfWeek": {"$dateFromString": {
                        "dateString": "$date"
                    }}}, 7]},
                    {"$gt": [{"$dayOfWeek": {"$dateFromString": {
                        "dateString": "$date"
                    }}}, 1]},
                ]
            },
            {"$cond": ["$half_day", 0.5, 1]},
            0,
        ]
    }


# ----------------------------- Employee monthly -----------------------------

@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    emp = get_employee(emp_code)
    start, end = validate_month(month)
    effective_start = max(start, date.fromisoformat(emp["joined_on"]))
    days = working_days(effective_start, end)

    pipeline = [
        {"$match": {"emp_code": emp_code, "date": {
            "$gte": start.isoformat(), "$lte": end.isoformat()
        }}},
        {"$set": {"weekday": {"$dayOfWeek": {
            "$dateFromString": {"dateString": "$date"}
        }}}},
        {"$group": {
            "_id": None,
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$status", list(PRESENCE)]},
                    {"$gt": ["$weekday", 1]},
                    {"$lt": ["$weekday", 7]},
                ]},
                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]},
                0,
            ]}},
            "leave_days": {"$sum": {"$cond": [
                {"$eq": ["$status", "LEAVE"]}, 1, 0
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0
            ]}},
            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "total_overtime_minutes": {"$sum": {
                "$ifNull": ["$overtime_minutes", 0]
            }},
        }},
    ]
    result = list(attendance_logs.aggregate(pipeline))
    values = result[0] if result else {}
    present = round_half_up(values.get("present_days", 0), 2)
    pct = round_half_up(present / days * 100, 2) if days else None

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": days,
        "present_days": present,
        "leave_days": values.get("leave_days", 0),
        "late_count": values.get("late_count", 0),
        "total_late_minutes": values.get("total_late_minutes", 0),
        "total_overtime_minutes": values.get("total_overtime_minutes", 0),
        "attendance_pct": pct,
    }


# ---------------------------- Department summary ----------------------------

@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start, end = month_filter(month)

    employee_match = {"joined_on": {"$lte": end}}
    if department is not None:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"code": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$code"]},
                    {"$gte": ["$date", start]},
                    {"$lte": ["$date", end]},
                ]}}},
            ],
            "as": "logs",
        }},
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {"$group": {
            "_id": "$department",
            "headcount_codes": {"$addToSet": "$emp_code"},
            "present_days": {"$sum": {"$cond": [
                {"$and": [
                    {"$in": ["$logs.status", list(PRESENCE)]},
                    {"$gt": [{"$dayOfWeek": {"$dateFromString": {
                        "dateString": "$logs.date"
                    }}}, 1]},
                    {"$lt": [{"$dayOfWeek": {"$dateFromString": {
                        "dateString": "$logs.date"
                    }}}, 7]},
                ]},
                {"$cond": [
                    {"$ifNull": ["$logs.half_day", False]}, 0.5, 1
                ]},
                0,
            ]}},
            "hours": {"$push": {"$cond": [
                {"$and": [
                    {"$in": ["$logs.status", list(PRESENCE)]},
                    {"$ne": ["$logs.work_hours", None]},
                ]},
                "$logs.work_hours",
                None,
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0
            ]}},
            "total_late_minutes": {"$sum": {
                "$ifNull": ["$logs.late_minutes", 0]
            }},
            "leave_count": {"$sum": {"$cond": [
                {"$eq": ["$logs.status", "LEAVE"]}, 1, 0
            ]}},
            "on_duty_count": {"$sum": {"$cond": [
                {"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0
            ]}},
        }},
        {"$set": {
            "headcount": {"$size": "$headcount_codes"},
            "avg_work_hours": {"$avg": "$hours"},
        }},
        {"$match": {"headcount": {"$gt": 0}}},
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1,
        }},
        {"$sort": {"department": 1}},
    ]

    items = list(employees.aggregate(pipeline))
    for item in items:
        item["present_days"] = round_half_up(item["present_days"], 2)
        item["avg_work_hours"] = round_half_up(item["avg_work_hours"], 2)
    return {"month": month, "items": items}


# ------------------------------ Late leaderboard ---------------------------

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str,
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None,
):
    start, end = month_filter(month)
    pipeline = [
        {"$match": {"date": {"$gte": start, "$lte": end},
                    "late_minutes": {"$gt": 0}}},
        {"$group": {
            "_id": "$emp_code",
            "total_late_minutes": {"$sum": "$late_minutes"},
            "late_count": {"$sum": 1},
        }},
        {"$lookup": {
            "from": "employees",
            "localField": "_id",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
    ]
    if department is not None:
        pipeline.append({"$match": {"employee.department": department}})

    pipeline += [
        {"$set": {
            "emp_code": "$_id",
            "name": "$employee.name",
            "department": "$employee.department",
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
        {"$project": {
            "_id": 0, "rank": 1, "emp_code": 1, "name": 1,
            "department": 1, "total_late_minutes": 1, "late_count": 1,
        }},
    ]
    return {"month": month, "items": list(attendance_logs.aggregate(pipeline))}


# ------------------------------- Department trend ---------------------------

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to"),
):
    start = validate_date(from_date)
    end = validate_date(to_date)
    if end < start:
        fail(422, "'to' must be on or after 'from'")
    if (end - start).days + 1 > 92:
        fail(422, "Date range cannot exceed 92 days")

    if not employees.find_one({"department": department}):
        fail(404, "Department not found")

    # Daily records are grouped in MongoDB. $densify fills missing days.
    pipeline = [
        {"$match": {"date": {"$gte": from_date, "$lte": to_date}}},
        {"$lookup": {
            "from": "employees",
            "localField": "emp_code",
            "foreignField": "emp_code",
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {"$group": {
            "_id": "$date",
            "present_count": {"$sum": {"$cond": [
                {"$in": ["$status", list(PRESENCE)]},
                {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]},
                0,
            ]}},
            "late_count": {"$sum": {"$cond": [
                {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0
            ]}},
        }},
        {"$project": {
            "_id": 0,
            "date": {"$dateFromString": {"dateString": "$_id"}},
            "present_count": 1,
            "late_count": 1,
        }},
        {"$densify": {
            "field": "date",
            "range": {"step": 1, "unit": "day",
                      "bounds": [
                          datetime.combine(start, time.min),
                          datetime.combine(end + timedelta(days=1), time.min),
                      ]},
        }},
        {"$set": {
            "date_string": {"$dateToString": {
                "format": "%Y-%m-%d", "date": "$date"
            }},
            "present_count": {"$ifNull": ["$present_count", 0]},
            "late_count": {"$ifNull": ["$late_count", 0]},
            "is_working_day": {"$and": [
                {"$ne": [{"$dayOfWeek": "$date"}, 1]},
                {"$ne": [{"$dayOfWeek": "$date"}, 7]},
            ]},
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"day": "$date_string"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$department", department]},
                    {"$lte": ["$joined_on", "$$day"]},
                ]}}},
                {"$count": "count"},
            ],
            "as": "hc",
        }},
        {"$set": {"headcount": {
            "$ifNull": [{"$arrayElemAt": ["$hc.count", 0]}, 0]
        }}},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            {"$divide": ["$present_count", "$headcount"]},
            None,
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate",
                "window": {"documents": [-6, 0]},
            }},
        }},
        {"$sort": {"date": 1}},
        {"$project": {
            "_id": 0,
            "date": "$date_string",
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1,
        }},
    ]

    items = list(attendance_logs.aggregate(pipeline))
    for item in items:
        if item["attendance_rate"] is not None:
            item["attendance_rate"] = round_half_up(item["attendance_rate"], 4)
        if item["moving_avg_7d"] is not None:
            item["moving_avg_7d"] = round_half_up(item["moving_avg_7d"], 4)

    return {"department": department, "items": items}


# ------------------------------- Explain ------------------------------------

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    allowed = {
        "attendance_list", "employee_monthly", "department_summary",
        "late_leaderboard", "department_trend",
    }
    if endpoint not in allowed:
        fail(422, "Unsupported endpoint name")

    # Explain the matching MongoDB operation using executionStats.
    if endpoint == "attendance_list":
        query = {}
        if emp_code:
            query["emp_code"] = emp_code
        if status:
            query["status"] = status
        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to
        operation = {
            "find": "attendance_logs",
            "filter": query,
            "sort": {"date": -1, "emp_code": 1},
            "skip": (page - 1) * page_size,
            "limit": page_size,
        }
        collection = "attendance_logs"
  
    elif endpoint == "employee_monthly":
        if not emp_code or not month:
            fail(422, "emp_code and month are required")

        start, end = month_filter(month)
        operation = {
            "aggregate": "attendance_logs",
            "pipeline": [
                {"$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": start,
                        "$lte": end,
                    },
                }},
                {"$set": {
                    "weekday": {
                        "$dayOfWeek": {
                            "$dateFromString": {"dateString": "$date"}
                        }
                    }
                }},
                {"$group": {
                    "_id": None,
                    "present_days": {"$sum": {"$cond": [
                        {"$and": [
                            {"$in": ["$status", list(PRESENCE)]},
                            {"$gt": ["$weekday", 1]},
                            {"$lt": ["$weekday", 7]},
                        ]},
                        {"$cond": [
                            {"$ifNull": ["$half_day", False]}, 0.5, 1
                        ]},
                        0,
                    ]}},
                    "leave_days": {"$sum": {"$cond": [
                        {"$eq": ["$status", "LEAVE"]}, 1, 0
                    ]}},
                    "late_count": {"$sum": {"$cond": [
                        {"$gt": [
                            {"$ifNull": ["$late_minutes", 0]}, 0
                        ]}, 1, 0
                    ]}},
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    },
                    "total_overtime_minutes": {
                        "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                    },
                }},
            ],
            "cursor": {},
        }
        collection = "attendance_logs"

    
    elif endpoint == "department_summary":
        if not month:
            fail(422, "month is required")

        start, end = month_filter(month)

        employee_match = {"joined_on": {"$lte": end}}
        if department is not None:
            employee_match["department"] = department

        operation = {
            "aggregate": "employees",
            "pipeline": [
                {"$match": employee_match},
                {"$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {"$match": {"$expr": {"$and": [
                            {"$eq": ["$emp_code", "$$code"]},
                            {"$gte": ["$date", start]},
                            {"$lte": ["$date", end]},
                        ]}}},
                    ],
                    "as": "logs",
                }},
                {"$unwind": {
                    "path": "$logs",
                    "preserveNullAndEmptyArrays": True,
                }},
                {"$group": {
                    "_id": "$department",
                    "headcount_codes": {"$addToSet": "$emp_code"},
                    "present_days": {"$sum": {"$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", list(PRESENCE)]},
                            {"$gt": [{"$dayOfWeek": {"$dateFromString": {
                                "dateString": "$logs.date"
                            }}}, 1]},
                            {"$lt": [{"$dayOfWeek": {"$dateFromString": {
                                "dateString": "$logs.date"
                            }}}, 7]},
                        ]},
                        {"$cond": [
                            {"$ifNull": ["$logs.half_day", False]}, 0.5, 1
                        ]},
                        0,
                    ]}},
                    "hours": {"$push": {"$cond": [
                        {"$and": [
                            {"$in": ["$logs.status", list(PRESENCE)]},
                            {"$ne": ["$logs.work_hours", None]},
                        ]},
                        "$logs.work_hours",
                        None,
                    ]}},
                    "late_count": {"$sum": {"$cond": [
                        {"$gt": [
                            {"$ifNull": ["$logs.late_minutes", 0]}, 0
                        ]}, 1, 0
                    ]}},
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                    },
                    "leave_count": {"$sum": {"$cond": [
                        {"$eq": ["$logs.status", "LEAVE"]}, 1, 0
                    ]}},
                    "on_duty_count": {"$sum": {"$cond": [
                        {"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0
                    ]}},
                }},
                {"$set": {
                    "headcount": {"$size": "$headcount_codes"},
                    "avg_work_hours": {"$avg": "$hours"},
                }},
                {"$match": {"headcount": {"$gt": 0}}},
                {"$project": {
                    "_id": 0,
                    "department": "$_id",
                    "headcount": 1,
                    "present_days": 1,
                    "avg_work_hours": 1,
                    "late_count": 1,
                    "total_late_minutes": 1,
                    "leave_count": 1,
                    "on_duty_count": 1,
                }},
                {"$sort": {"department": 1}},
            ],
            "cursor": {},
        }
        collection = "employees"

    
    elif endpoint == "late_leaderboard":
        if not month:
            fail(422, "month is required")

        start, end = month_filter(month)

        pipeline = [
            {"$match": {
                "date": {"$gte": start, "$lte": end},
                "late_minutes": {"$gt": 0},
            }},
            {"$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"},
                "late_count": {"$sum": 1},
            }},
            {"$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee",
            }},
            {"$unwind": "$employee"},
        ]

        if department is not None:
            pipeline.append({
                "$match": {"employee.department": department}
            })

        pipeline += [
            {"$set": {
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
            }},
            {"$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {"rank": {"$rank": {}}},
            }},
            {"$match": {"rank": {"$lte": limit}}},
            {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
            {"$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": 1,
                "name": 1,
                "department": 1,
                "total_late_minutes": 1,
                "late_count": 1,
            }},
        ]

        operation = {
            "aggregate": "attendance_logs",
            "pipeline": pipeline,
            "cursor": {},
        }
        collection = "attendance_logs"

    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            fail(422, "department, from and to are required")

        validate_date(from_date)
        validate_date(to_date)

        operation = {
            "aggregate": "attendance_logs",
            "pipeline": [
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "emp_code",
                        "foreignField": "emp_code",
                        "as": "employee",
                    }
                },
                {"$unwind": "$employee"},
                {
                    "$match": {
                        "employee.department": department,
                        "date": {"$gte": from_date, "$lte": to_date},
                    }
                },
                {"$sort": {"date": 1}},
            ],
            "cursor": {},
        }
        collection = "attendance_logs"

    try:
        result = db.command({
            "explain": operation,
            "verbosity": "executionStats",
        })
        return {
            "endpoint": endpoint,
            "collection": collection,
            "explain": json_util.loads(json_util.dumps(result)),
        }
    except PyMongoError as exc:
        fail(422, f"Unable to explain query: {str(exc)}")
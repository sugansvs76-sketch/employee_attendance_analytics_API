
from urllib import response
from urllib import response
from uuid import uuid4

from fastapi.testclient import TestClient
from app.main import app
import random

client = TestClient(app)


def test_department_summary():
    response = client.get(
        "/analytics/departments/summary",
        params={"month": "2026-10"}
    )

    assert response.status_code == 200, response.text

    data = response.json()
    print("Department summary response:", data)

    assert isinstance(data, (dict, list)), data



def test_late_arrival_leaderboard():
    response = client.get(
        "/analytics/leaderboard/late",
        params={"month": "2026-10"}
    )

    assert response.status_code == 200, response.text

    data = response.json()
    print("Late arrival leaderboard response:", data)

    assert isinstance(data, (dict, list)), data

    

def test_department_attendance_trend():
    response = client.get(
        "/analytics/departments/Engineering/trend",
        params={
            "from": "2026-10-01",
            "to": "2026-10-31"
        }
    )

    assert response.status_code == 200, response.text

    data = response.json()
    print("Department attendance trend response:", data)

    assert isinstance(data, (dict, list)), data


def test_admin_explain_endpoint():
    response = client.get("/admin/explain/attendance_list")

    assert response.status_code == 200, response.text

    data = response.json()
    print("Admin explain response:", data)

    assert isinstance(data, dict), data




def test_employee_monthly_analytics():
    from uuid import uuid4

    unique_id = str(uuid4().int % 900000 + 100000)
    emp_code = f"EMP{unique_id}"

    employee_payload = {
        "emp_code": emp_code,
        "name": "Monthly Analytics Test Employee",
        "email": f"monthly.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    # Step 1: Create employee
    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    # Step 2: Request analytics for October 2026
    response = client.get(
        f"/analytics/employees/{emp_code}/monthly",
        params={"month": "2026-10"}
    )

    # Step 3: Verify response
    assert response.status_code == 200, response.text

    data = response.json()
    print("Monthly analytics response:", data)

    assert isinstance(data, dict), data

def test_punch_in():
    # Create a unique employee for this test
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Punch Test Employee",
        "email": f"punch.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code in (200, 201), create_response.text

    punch_payload = {
        "emp_code": employee_payload["emp_code"]
    }

    response = client.post("/attendance/punch-in", json=punch_payload)

    assert response.status_code == 201, response.text



def test_list_employees():
    response = client.get("/employees")

    assert response.status_code == 200, response.text

    data = response.json()

    assert "items" in data, "Response is missing 'items'"
    assert "total" in data, "Response is missing 'total'"
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)



def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200

    
def test_list_attendance():
    response = client.get("/attendance")

    assert response.status_code == 200, response.text

    data = response.json()
    assert "items" in data
    assert "total" in data
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)

    


def test_regularize_attendance():
    from uuid import uuid4
    from datetime import datetime, timezone, timedelta

    unique_id = str(uuid4().int % 900000 + 100000)
    emp_code = f"EMP{unique_id}"

    employee_payload = {
        "emp_code": emp_code,
        "name": "Regularization Test Employee",
        "email": f"regularize.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    # Create employee
    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    # Punch in
    punch_response = client.post(
        "/attendance/punch-in",
        json={"emp_code": emp_code}
    )
    assert punch_response.status_code == 201, punch_response.text

    # Get today's date in IST
    ist = timezone(timedelta(hours=5, minutes=30))
    today = datetime.now(ist).strftime("%Y-%m-%d")

    # Correct attendance status
    patch_response = client.patch(
        f"/attendance/{emp_code}/{today}",
        json={
            "status": "WFH",
            "reason": "Worked from home with approval",
            "regularized_by": "HR_ADMIN"
        }
    )

    assert patch_response.status_code == 200, patch_response.text



    

def test_punch_out():
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Punch Out Test Employee",
        "email": f"punchout.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    emp_code = employee_payload["emp_code"]

    # Whole-second UTC timestamps, exactly one minute apart.
    from datetime import datetime, timezone

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    punch_in_ms = (now_ms // 1000) * 1000
    punch_out_ms = punch_in_ms + 60_000

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": punch_in_ms,
        },
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    punch_out_response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": emp_code,
            "punched_at": punch_out_ms,
        },
    )
    assert punch_out_response.status_code == 200, punch_out_response.text

    result = punch_out_response.json()
    assert result["punch_in"] == punch_in_ms
    assert result["punch_out"] == punch_out_ms
    assert result["work_hours"] == 0.02

    
def test_duplicate_punch_in():
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Duplicate Punch Test Employee",
        "email": f"duplicate.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    # Step 1: Create employee
    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    punch_payload = {
        "emp_code": employee_payload["emp_code"]
    }

    # Step 2: First punch-in should succeed
    first_response = client.post(
        "/attendance/punch-in",
        json=punch_payload
    )
    assert first_response.status_code == 201, first_response.text

    # Step 3: Second punch-in should be rejected
    second_response = client.post(
        "/attendance/punch-in",
        json=punch_payload
    )
    assert second_response.status_code == 409, second_response.text



def test_punch_out_without_punch_in():
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Punch Out Test Employee",
        "email": f"punchout.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    # Create employee
    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    # Attempt punch-out without punch-in
    response = client.post(
        "/attendance/punch-out",
        json={"emp_code": employee_payload["emp_code"]}
    )

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "No open punch-in record found"


def test_overtime_exactly_30_minutes():
    from datetime import datetime, timezone
    from uuid import uuid4

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Overtime Test Employee",
        "email": f"overtime.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    # Use UTC epoch milliseconds, truncated to whole seconds.
    shift_end_ms = int(
        datetime(2026, 10, 10, 13, 0, tzinfo=timezone.utc).timestamp()
        * 1000
    )

    punch_in_ms = shift_end_ms - (9 * 60 * 60 * 1000)
    punch_out_ms = shift_end_ms + (30 * 60 * 1000)

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_in_ms
        }
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    punch_out_response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_out_ms
        }
    )
    assert punch_out_response.status_code == 200, punch_out_response.text

    data = punch_out_response.json()
    print("Overtime response:", data)

    
def test_half_day_attendance():
    from datetime import datetime, timezone
    from uuid import uuid4

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Half Day Test Employee",
        "email": f"halfday.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    create_response = client.post(
        "/employees",
        json=employee_payload
    )
    assert create_response.status_code in (200, 201), create_response.text

    # Punch in at 09:30 UTC and punch out 4 hours later.
    punch_in_ms = int(
        datetime(2026, 10, 10, 9, 30, tzinfo=timezone.utc).timestamp()
        * 1000
    )
    punch_out_ms = punch_in_ms + (4 * 60 * 60 * 1000)

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_in_ms
        }
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    punch_out_response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_out_ms
        }
    )
    assert punch_out_response.status_code == 200, punch_out_response.text

    data = punch_out_response.json()
    print("Half-day attendance response:", data)

    


def test_punch_in_exactly_10_minutes_after_shift_start():
    import random
    from datetime import datetime, timezone

    emp_code = f"EMP{random.randint(100000, 999999)}"
    email = f"late10test{emp_code}@example.com"


    create_response = client.post(
        "/employees",
        json={
            "emp_code": emp_code,
            "name": "Late Boundary Test",
            "email": email,
            "department": "Engineering",
            "shift_start": "09:30",
            "shift_end": "18:30",
            "joined_on": "2026-10-01"
        }
    )

    assert create_response.status_code in (200, 201), create_response.text

    # 09:40 IST = 04:10 UTC
    punch_in_ms = int(
        datetime(
            2026, 10, 10, 4, 10, 0,
            tzinfo=timezone.utc
        ).timestamp() * 1000
    )

   
    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": emp_code,
            "punched_at": punch_in_ms
        }
    )

    assert response.status_code == 201, response.text

    data = response.json()
    print("Exactly 10 minutes late:", data)

    assert data.get("late_minutes") == 0, data




def test_list_employees_pagination():
    response = client.get(
        "/employees",
     params={"page": 1, "page_size": 5}
    )

    assert response.status_code == 200, response.text

    data = response.json()
    print("Employee pagination response:", data)

    assert isinstance(data, dict), data
    assert isinstance(data.get("items"), list), data
    assert isinstance(data.get("total"), int), data
    assert len(data["items"]) <= 5


def test_create_employee():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Pytest Employee",
        "email": f"pytest.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"


    }

    response = client.post("/employees", json=payload)

    assert response.status_code in (200, 201), (
        f"Status: {response.status_code}; Response: {response.text}"
    )

    data = response.json()
    assert data["emp_code"] == payload["emp_code"]

    
def test_duplicate_employee_code():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Duplicate Test Employee",
        "email": f"duplicate.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    # Create the employee for the first time
    first_response = client.post("/employees", json=payload)

    assert first_response.status_code == 201, (
        f"Status: {first_response.status_code}; "
        f"Response: {first_response.text}"
    )

    # Try creating another employee with the same employee code
    duplicate_payload = {
        **payload,
        "email": f"duplicate2.{unique_id}@example.com"
    }

    second_response = client.post("/employees", json=duplicate_payload)

    assert second_response.status_code == 409, (
        f"Expected 409 Conflict, got {second_response.status_code}; "
        f"Response: {second_response.text}"
    )


def test_duplicate_employee_email():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Email Test Employee",
        "email": f"emailtest.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    first_response = client.post("/employees", json=payload)
    assert first_response.status_code == 201, first_response.text

    duplicate_payload = {
        **payload,
        "emp_code": f"EMP{int(unique_id) + 1}"
    }

    second_response = client.post("/employees", json=duplicate_payload)

    assert second_response.status_code == 409, (
        f"Expected 409 Conflict, got {second_response.status_code}: "
        f"{second_response.text}"
    )


def test_create_employee_missing_required_field():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}",
        # name is intentionally missing
        "email": f"missing.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    response = client.post("/employees", json=payload)

    assert response.status_code == 422, (
        f"Expected 422 validation error, got {response.status_code}: "
        f"{response.text}"
    )


def test_create_employee_invalid_email():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Invalid Email Employee",
        "email": "invalid-email",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01"
    }

    response = client.post("/employees", json=payload)

    assert response.status_code == 422, (
        f"Expected 422 validation error, got {response.status_code}: "
        f"{response.text}"
    )

def test_employee_list_empty_department():
    response = client.get(
        "/employees",
        params={"department": "NoSuchDepartmentForTest"}
    )

    assert response.status_code == 200, response.text

    data = response.json()

    assert data["items"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20


def test_employee_list_invalid_pagination():
    # Page cannot be zero
    response = client.get("/employees", params={"page": 0})
    assert response.status_code == 422, response.text

    # Page size cannot be zero
    response = client.get("/employees", params={"page_size": 0})
    assert response.status_code == 422, response.text

    # Page size cannot exceed 100
    response = client.get("/employees", params={"page_size": 101})
    assert response.status_code == 422, response.text


def test_punch_in_unknown_employee():
    unique_id = str(uuid4().int % 900000 + 100000)

    payload = {
        "emp_code": f"EMP{unique_id}"
    }

    response = client.post("/attendance/punch-in", json=payload)

    assert response.status_code in (400, 404), (
        f"Expected 400 or 404 for unknown employee, "
        f"got {response.status_code}: {response.text}"
    )

def test_punch_out_twice():
    from datetime import datetime, timezone

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Repeated Punch Out Employee",
        "email": f"repeatout.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    emp_code = employee_payload["emp_code"]

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    punch_in_ms = (now_ms // 1000) * 1000
    punch_out_ms = punch_in_ms + 60_000

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={"emp_code": emp_code, "punched_at": punch_in_ms},
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    first_punch_out = client.post(
        "/attendance/punch-out",
        json={"emp_code": emp_code, "punched_at": punch_out_ms},
    )
    assert first_punch_out.status_code == 200, first_punch_out.text

    second_punch_out = client.post(
        "/attendance/punch-out",
        json={"emp_code": emp_code, "punched_at": punch_out_ms + 60_000},
    )

    assert second_punch_out.status_code in (400, 409, 422), (
        f"Expected rejection of second punch-out, got "
        f"{second_punch_out.status_code}: {second_punch_out.text}"
    )


def test_punch_out_before_punch_in():
    from datetime import datetime, timezone

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Invalid Time Test Employee",
        "email": f"invalidtime.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    emp_code = employee_payload["emp_code"]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    punch_in_ms = (now_ms // 1000) * 1000

    punch_in_response = client.post(
        "/attendance/punch-in",
        json={"emp_code": emp_code, "punched_at": punch_in_ms},
    )
    assert punch_in_response.status_code == 201, punch_in_response.text

    
    punch_out_response = client.post(
        "/attendance/punch-out",
        json={"emp_code": emp_code, "punched_at": punch_in_ms - 60_000},
    )

    assert punch_out_response.status_code in (400, 404, 409, 422), (
        f"Expected rejection of punch-out before punch-in, got "
        f"{punch_out_response.status_code}: {punch_out_response.text}"
    )


def test_punch_in_invalid_timestamp():
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Invalid Timestamp Employee",
        "email": f"invalidtimestamp.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": "not-a-timestamp",
        },
    )

    assert response.status_code == 422, (
        f"Expected 422 for invalid timestamp, got "
        f"{response.status_code}: {response.text}"
    )

def test_punch_out_invalid_timestamp():
    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Invalid Punch Out Timestamp Employee",
        "email": f"invalidpunchout.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    response = client.post(
        "/attendance/punch-out",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": "not-a-timestamp",
        },
    )

    assert response.status_code == 422, (
        f"Expected 422 for invalid timestamp, got "
        f"{response.status_code}: {response.text}"
    )


def test_attendance_unknown_employee():
    unique_id = str(uuid4().int % 900000 + 100000)
    emp_code = f"EMP{unique_id}"

    response = client.get(
        "/attendance",
        params={"emp_code": emp_code},
    )

    assert response.status_code == 200, response.text

    result = response.json()
    assert result["items"] == []
    assert result["total"] == 0


def test_attendance_invalid_pagination():
    response_page = client.get("/attendance", params={"page": 0})
    assert response_page.status_code == 422, response_page.text

    response_page_size = client.get(
        "/attendance",
        params={"page_size": 0},
    )
    assert response_page_size.status_code == 422, response_page_size.text

    response_large_page_size = client.get(
        "/attendance",
        params={"page_size": 101},
    )
    assert response_large_page_size.status_code == 422, (
        response_large_page_size.text
    )


def test_attendance_filter_by_employee():
    from datetime import datetime, timezone

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Attendance Filter Test Employee",
        "email": f"attendancefilter.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    punch_in_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    punch_in_ms = (punch_in_ms // 1000) * 1000

    punch_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_in_ms,
        },
    )
    assert punch_response.status_code == 201, punch_response.text

    response = client.get(
        "/attendance",
        params={"emp_code": employee_payload["emp_code"]},
    )
    assert response.status_code == 200, response.text

    result = response.json()
    assert result["total"] >= 1
    assert all(
        item["emp_code"] == employee_payload["emp_code"]
        for item in result["items"]
    )


def test_attendance_filter_by_date():
    from datetime import datetime, timezone

    unique_id = str(uuid4().int % 900000 + 100000)

    employee_payload = {
        "emp_code": f"EMP{unique_id}",
        "name": "Attendance Date Filter Employee",
        "email": f"datefilter.{unique_id}@example.com",
        "department": "Engineering",
        "shift_start": "09:30",
        "shift_end": "18:30",
        "joined_on": "2026-10-01",
    }

    create_response = client.post("/employees", json=employee_payload)
    assert create_response.status_code == 201, create_response.text

    punch_in_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    punch_in_ms = (punch_in_ms // 1000) * 1000

    punch_response = client.post(
        "/attendance/punch-in",
        json={
            "emp_code": employee_payload["emp_code"],
            "punched_at": punch_in_ms,
        },
    )
    assert punch_response.status_code == 201, punch_response.text

    response = client.get(
        "/attendance",
        params={
            "emp_code": employee_payload["emp_code"],
            "day": datetime.fromtimestamp(
                punch_in_ms / 1000, timezone.utc
            ).date().isoformat(),
        },
    )

    assert response.status_code == 200, response.text

"""
End-to-end check against a real PostgreSQL database (Supabase included).

Unlike the unit tests, this exercises the actual SQL, the timezone handling
and the full booking lifecycle. It creates rows under a reserved test phone
number and deletes them again before exiting.

    DATABASE_URL="postgresql+asyncpg://USER:PASS@HOST:5432/postgres" \
        python3 scripts/integration_test.py

Exits non-zero if any check fails.
"""
import asyncio
import pathlib
import sys
from datetime import date, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.config import settings
from app.services import availability, booking
from app.core.models.business import Business

TEST_PHONE = "972500000999"

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((status, name, detail))
    print(f"[{status}] {name}" + (f"  -- {detail}" if detail else ""))
    return condition


async def main():
    print("=" * 70)
    print("BUSINESS ID CHECK")
    print("=" * 70)

    async with availability.async_session() as s:
        rows = (await s.execute(select(Business))).scalars().all()
        db_ids = [str(b.id) for b in rows]

    print(f"business rows in DB : {db_ids}")
    print(f"hardcoded BUSINESS_ID: {settings.BUSINESS_ID}")
    check(
        "hardcoded BUSINESS_ID matches a real business row",
        settings.BUSINESS_ID in db_ids,
        "services filter every query by this id",
    )

    print()
    print("=" * 70)
    print("READ PATHS")
    print("=" * 70)

    treatments = await availability.get_treatments()
    check("get_treatments() returns rows", len(treatments) > 0, f"got {len(treatments)}")

    therapists = await availability.get_therapists()
    check("get_therapists() returns rows", len(therapists) > 0, f"got {len(therapists)}")

    summary = await availability.get_treatments_summary()
    check("get_treatments_summary() is not the empty-state string",
          "אין טיפולים" not in summary, repr(summary[:60]))

    tsummary = await availability.get_therapists_summary()
    check("get_therapists_summary() is populated", bool(tsummary), repr(tsummary[:60]))

    if not treatments:
        print("\n>>> Stopping: no treatments visible, nothing else can be tested.")
        return summarize()

    print()
    print("=" * 70)
    print("AVAILABILITY")
    print("=" * 70)

    treatment = treatments[0]
    print(f"using treatment: {treatment['name']} ({treatment['duration_minutes']}min)")

    # Find a weekday the therapists actually work.
    target = date.today() + timedelta(days=1)
    for _ in range(10):
        slots = await availability.get_available_slots(treatment["id"], target)
        if slots:
            break
        target += timedelta(days=1)

    check("get_available_slots() finds slots on some upcoming day",
          len(slots) > 0, f"{target} ({target.strftime('%A')}): {len(slots)} slots")

    if not slots:
        return summarize()

    print(f"first 5 slots: {[s['time'] for s in slots[:5]]}")

    nxt = await availability.find_next_available_date(treatment["id"], target)
    check("find_next_available_date() returns a date", nxt is not None, str(nxt))

    print()
    print("=" * 70)
    print("BOOKING LIFECYCLE")
    print("=" * 70)

    slot = slots[0]
    date_str = target.strftime("%Y-%m-%d")

    res = await booking.create_appointment(
        customer_phone=TEST_PHONE,
        treatment_name=treatment["name"],
        therapist_name=slot["therapist_name"],
        appointment_date=date_str,
        appointment_time=slot["time"],
    )
    check("create_appointment() succeeds", res.get("success"), str(res))
    appt_id = res.get("appointment_id")

    if res.get("success"):
        # The slot must disappear from availability for that therapist.
        after = await availability.get_available_slots(treatment["id"], target)
        still_there = any(
            s["time"] == slot["time"] and s["therapist_id"] == slot["therapist_id"]
            for s in after
        )
        check("booked slot no longer offered", not still_there,
              f"{slot['time']} with {slot['therapist_name']}")

        # Double booking must be refused.
        dup = await booking.create_appointment(
            customer_phone="972500000998",
            treatment_name=treatment["name"],
            therapist_name=slot["therapist_name"],
            appointment_date=date_str,
            appointment_time=slot["time"],
        )
        check("double booking refused with slot_taken",
              dup.get("error") == "slot_taken", str(dup))

        # Ambiguous treatment name must not raise (the MultipleResultsFound bug).
        amb = await booking.create_appointment(
            customer_phone=TEST_PHONE,
            treatment_name="עיסוי",
            therapist_name="",
            appointment_date=date_str,
            appointment_time=slots[-1]["time"],
        )
        check("ambiguous treatment name does not crash",
              amb.get("error") != "internal_error", str(amb))
        amb_id = amb.get("appointment_id")

        # Past dates refused.
        past = await booking.create_appointment(
            customer_phone=TEST_PHONE,
            treatment_name=treatment["name"],
            therapist_name="",
            appointment_date="2020-01-01",
            appointment_time="10:00",
        )
        check("past date refused", past.get("error") == "in_the_past", str(past))

        # Listing.
        mine = await booking.get_customer_appointments(TEST_PHONE)
        check("get_customer_appointments() lists the booking", len(mine) >= 1,
              f"{len(mine)} appointment(s)")

        # A time written must read back as the same wall-clock time, or the
        # customer gets told an hour that is off by the UTC offset.
        listed_times = [a["time"] for a in mine]
        check("booked time round-trips unchanged", slot["time"] in listed_times,
              f"booked {slot['time']}, listing shows {listed_times}")

        # Reschedule to a later free slot.
        later = [s for s in after if s["therapist_id"] == slot["therapist_id"]]
        if later:
            new_slot = later[-1]
            resc = await booking.reschedule_appointment(
                customer_phone=TEST_PHONE,
                new_date=date_str,
                new_time=new_slot["time"],
                appointment_id=appt_id,
            )
            check("reschedule_appointment() succeeds", resc.get("success"),
                  f"{slot['time']} -> {new_slot['time']} : {resc}")

        # Cancel.
        canc = await booking.cancel_appointment(TEST_PHONE, appointment_id=appt_id)
        check("cancel_appointment() succeeds", canc.get("success"), str(canc))

        if later:
            check("cancellation reports the rescheduled time, not UTC",
                  canc.get("time") == new_slot["time"],
                  f"rescheduled to {new_slot['time']}, cancel said {canc.get('time')}")

        # Cancelled slot frees up again.
        freed = await availability.get_available_slots(treatment["id"], target)
        check("cancelling frees the slot", len(freed) > len(after),
              f"{len(after)} -> {len(freed)} slots")

        # Cleanup
        await cleanup([appt_id, amb_id, dup.get("appointment_id")])

    await check_reminders(treatment)
    await check_session_persistence()
    await check_audit_trail(treatment)
    await check_dashboard_api(treatment)
    await check_two_clinics()
    return summarize()


async def check_audit_trail(treatment):
    """
    Drive a real conversation and read the audit trail it leaves behind.

    Only the model and WhatsApp are stubbed; the database work is real, so
    the trace is the one production would produce.
    """
    print()
    print("=" * 70)
    print("AUDIT TRAIL")
    print("=" * 70)

    from app.core import audit
    from app.core.audit import MemorySink, group_traces, set_sink
    from app.services import conversation
    from app.services.audit_analysis import ERROR, analyze, report

    sink = MemorySink()
    previous_sink = set_sink(sink)

    real_ai = conversation.process_message
    real_send = conversation.send_message
    sent = []

    async def fake_ai(**kwargs):
        return {
            "intention": "check_availability",
            "next_state": "choosing_time",
            "treatment": treatment["name"],
            "date": "מחר",
            "time": None,
            "therapist": None,
            "language": "he",
            "response": "בטח, הנה מה שפנוי",
        }

    async def fake_send(phone, text):
        sent.append((phone, text))
        return True

    conversation.process_message = fake_ai
    conversation.send_message = fake_send

    try:
        await conversation.handle_message(TEST_PHONE, "מה פנוי מחר")

        traces = group_traces(sink.events)
        check("a conversation produces exactly one trace", len(traces) == 1,
              f"{len(traces)} traces, {len(sink.events)} events")

        if not traces:
            return

        events = list(traces.values())[0]
        recorded = [e.operation for e in events]
        print(f"operations: {' -> '.join(recorded)}")

        for expected in ("message.received", "session.load", "clinic.load",
                         "ai.decision", "availability.lookup",
                         "session.save", "whatsapp.send", "message.handled"):
            check(f"trail records {expected}", expected in recorded)

        check("every event carries timing",
              all(e.duration_ms >= 0 for e in events))
        check("events are sequentially numbered",
              [e.seq for e in events] == list(range(1, len(events) + 1)))

        lookup = next((e for e in events if e.operation == "availability.lookup"), None)
        check("availability inputs were captured",
              bool(lookup and lookup.inputs.get("booking")),
              str(lookup.inputs if lookup else None))

        send = next((e for e in events if e.operation == "whatsapp.send"), None)
        check("the reply that went out was captured",
              bool(send and send.outputs.get("delivered")),
              str(send.outputs if send else None))

        check("the customer's number is never written in full",
              all(TEST_PHONE not in f"{e.inputs}{e.outputs}" for e in events))

        findings = analyze(sink.events)
        errors = [f for f in findings if f.severity == ERROR]
        check("a healthy conversation raises no errors", not errors,
              "; ".join(str(f) for f in errors) or "none")

        # Now break it on purpose: the analyzers must notice.
        sink.clear()
        conversation.send_message = lambda phone, text: fail_to_send()

        async def fail_to_send_async(phone, text):
            return False

        conversation.send_message = fail_to_send_async
        await conversation.handle_message(TEST_PHONE, "מה פנוי מחר")

        broken = analyze(sink.events)
        broken_codes = {f.code for f in broken}
        check("a failed delivery is detected",
              "reply_not_delivered" in broken_codes,
              str(sorted(broken_codes)))

        summary = report(sink.events)
        check("the report counts what it found",
              summary["traces"] == 1 and summary["counts"][ERROR] >= 1,
              str(summary["counts"]))

    finally:
        conversation.process_message = real_ai
        conversation.send_message = real_send
        set_sink(previous_sink)
        await cleanup([])


def fail_to_send():
    raise AssertionError("unused")


async def check_session_persistence():
    """Conversation state must outlive the process, not just the request."""
    print()
    print("=" * 70)
    print("CONVERSATION STATE")
    print("=" * 70)

    from app.services import session_store
    from app.core.enums import ConversationState

    try:
        fresh = await session_store.load(TEST_PHONE)
        check("an unknown number starts idle with no history",
              fresh["state"] == ConversationState.IDLE and fresh["history"] == [],
              str(fresh))

        history = [
            {"role": "user", "content": "אני רוצה עיסוי שוודי"},
            {"role": "assistant", "content": "לאיזה יום"},
        ]
        booking = {"treatment": "עיסוי שוודי", "date": "2027-06-25"}
        await session_store.save(
            phone=TEST_PHONE, state=ConversationState.CHOOSING_DATE,
            history=history, booking=booking, language="he",
        )

        # A separate load is what a restarted process would do.
        again = await session_store.load(TEST_PHONE)
        check("state survives", again["state"] == ConversationState.CHOOSING_DATE,
              str(again["state"]))
        check("history survives", again["history"] == history,
              f"{len(again['history'])} messages")
        check("half-finished booking survives", again["booking"] == booking,
              str(again["booking"]))
        check("language is remembered for reminders", again["language"] == "he")

        await session_store.clear_booking(TEST_PHONE)
        cleared = await session_store.load(TEST_PHONE)
        check("clearing the booking keeps the conversation",
              cleared["booking"] == {} and cleared["history"] == history,
              str(cleared["booking"]))

    finally:
        await cleanup([])


async def check_reminders(treatment):
    """Reminders, with WhatsApp stubbed out so nothing is actually sent."""
    print()
    print("=" * 70)
    print("REMINDERS")
    print("=" * 70)

    import uuid
    from app.services import reminders
    from app.core.models.appointment import Appointment
    from app.core.models.customer import Customer
    from app.core.timeutils import now as clinic_now

    sent = []

    async def fake_send(phone, text):
        sent.append((phone, text))
        return True

    real_send = reminders.send_message
    reminders.send_message = fake_send

    appt_id = None
    try:
        async with reminders.async_session() as s:
            res = await s.execute(select(Customer).where(Customer.phone == TEST_PHONE))
            customer = res.scalar_one_or_none()
            if not customer:
                customer = Customer(
                    id=uuid.uuid4(), business_id=settings.BUSINESS_ID,
                    phone=TEST_PHONE, language="he", conversation_state="idle",
                )
                s.add(customer)
                await s.flush()
            else:
                customer.language = "he"

            from app.core.models.therapist import Therapist
            therapist = (await s.execute(select(Therapist))).scalars().first()

            # The reminder run below is told it is ten years from now, so it
            # finds this appointment and nothing else. Run at today's date it
            # would also pick up every real appointment that happens to be
            # due, and with WhatsApp stubbed out their reminders would be
            # marked sent without ever reaching the customer.
            virtual_now = clinic_now() + timedelta(days=3653)
            # Placed squarely inside the 24 hour window.
            start = virtual_now + timedelta(hours=23)
            appt = Appointment(
                id=uuid.uuid4(), business_id=settings.BUSINESS_ID,
                customer_id=customer.id, therapist_id=therapist.id,
                treatment_id=treatment["id"], start_time=start,
                end_time=start + timedelta(minutes=60), status="confirmed",
                reminder_24h_sent=False, reminder_1h_sent=False,
            )
            s.add(appt)
            await s.commit()
            appt_id = appt.id

        result = await reminders.send_due_reminders(now=virtual_now)
        check("24h reminder is sent for an appointment 23h away",
              result["sent"]["24h"] == 1, str(result["sent"]))

        check("reminder text names the treatment",
              bool(sent) and treatment["name"] in sent[0][1],
              sent[0][1] if sent else "nothing sent")

        check("reminder goes to the customer's number",
              bool(sent) and sent[0][0] == TEST_PHONE)

        before = len(sent)
        again = await reminders.send_due_reminders(now=virtual_now)
        check("running again does not send a duplicate",
              again["total"] == 0 and len(sent) == before,
              f"second run sent {again['total']}")

        async with reminders.async_session() as s:
            row = await s.get(Appointment, appt_id)
            check("24h flag recorded in the database", row.reminder_24h_sent is True)
            check("1h flag left alone", row.reminder_1h_sent is False)

    finally:
        reminders.send_message = real_send
        if appt_id:
            async with reminders.async_session() as s:
                row = await s.get(Appointment, appt_id)
                if row:
                    await s.delete(row)
                await s.commit()
        await cleanup([])


async def check_dashboard_api(treatment):
    """The endpoints the dashboard relies on, called against the real database."""
    print()
    print("=" * 70)
    print("DASHBOARD API")
    print("=" * 70)

    from fastapi import HTTPException
    from app.api.v1 import appointments as api
    from app.api.v1 import business as business_api
    from app.core.schemas.api import AppointmentIn, AppointmentNotes
    from app.core.timeutils import now as clinic_now

    # A number typed the way a person would, Israeli local format.
    typed_phone = "050-000-0999"
    booked_id = None

    try:
        day = clinic_now().date() + timedelta(days=1)
        for _ in range(10):
            free = await api.availability(treatment_id=treatment["id"], date=day.isoformat())
            if free["slots"]:
                break
            day += timedelta(days=1)
        check("availability offers slots on a working day", bool(free["slots"]),
              f"{day}: {len(free['slots'])} slots")
        slot = free["slots"][0]

        created = await api.book_appointment(AppointmentIn(
            customer_phone=typed_phone, customer_name="Dashboard Test",
            treatment_name=treatment["name"], therapist_name=slot["therapist_name"],
            date=day.isoformat(), time=slot["time"], notes="first visit"))
        booked_id = created["id"]
        check("a typed local number is stored the way WhatsApp sends it",
              created["customer_phone"] == TEST_PHONE, created["customer_phone"])
        check("the name typed by the clinic is saved",
              created["customer_name"] == "Dashboard Test")
        check("the note is saved", created["notes"] == "first visit")
        check("ids and duration come back for the calendar",
              created["treatment_id"] == treatment["id"]
              and created["duration_minutes"] == treatment["duration_minutes"])

        after = await api.availability(treatment_id=treatment["id"], date=day.isoformat(),
                                       therapist_id=slot["therapist_id"])
        times = [s["time"] for s in after["slots"]]
        check("the booked time is no longer offered for that therapist",
              slot["time"] not in times, slot["time"])

        moving = await api.availability(treatment_id=treatment["id"], date=day.isoformat(),
                                        therapist_id=slot["therapist_id"],
                                        exclude_appointment_id=booked_id)
        check("excluding the booking offers its own time again, for moving it",
              slot["time"] in [s["time"] for s in moving["slots"]])

        past = await api.availability(treatment_id=treatment["id"], date="2020-01-01")
        check("a past date offers nothing", past["slots"] == [])

        edited = await api.edit_notes(booked_id, AppointmentNotes(notes="prefers firm pressure"))
        check("notes can be edited", edited["notes"] == "prefers firm pressure")
        cleared = await api.edit_notes(booked_id, AppointmentNotes(notes="   "))
        check("blank notes are cleared", cleared["notes"] is None)

        by_phone = await api.list_appointments(customer_phone=typed_phone, from_date=None,
                                               to_date=None, status=None, therapist_id=None,
                                               limit=50)
        check("filtering by a typed number finds the customer's history",
              any(a["id"] == booked_id for a in by_phone), f"{len(by_phone)} found")

        # A month on the calendar must not cost a query per appointment.
        from sqlalchemy import event
        from app.core.db import engine
        statements = []

        def count(*args):
            statements.append(1)

        event.listen(engine.sync_engine, "before_cursor_execute", count)
        try:
            listed = await api.list_appointments(
                from_date=(day - timedelta(days=60)).isoformat(),
                to_date=(day + timedelta(days=60)).isoformat(),
                status=None, therapist_id=None, customer_phone=None, limit=1000)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", count)
        check("listing appointments costs the same few queries however many there are",
              len(statements) <= 4, f"{len(listed)} appointments, {len(statements)} queries")

        from app.api.v1 import therapists as therapists_api
        team = await therapists_api.list_therapists(include_inactive=True)
        check("therapists say when they joined, so their colours stay put",
              all(t["created_at"] for t in team), f"{len(team)} therapists")

        stats = await api.statistics(from_date=day.isoformat(), to_date=day.isoformat())
        today_row = stats["by_day"][0] if stats["by_day"] else {}
        check("statistics count the booking per day",
              len(stats["by_day"]) == 1 and today_row.get("appointments", 0) >= 1,
              str(today_row))
        check("revenue is the treatment's price",
              stats["revenue"] >= treatment["price"], f"{stats['revenue']}")

        wide = await api.statistics(from_date="2027-01-01", to_date="2027-01-31")
        check("every day of a range is present, with zeros", len(wide["by_day"]) == 31)

        try:
            await api.statistics(from_date="2025-01-01", to_date="2027-01-01")
            check("an over-long range is refused", False)
        except HTTPException as e:
            check("an over-long range is refused", e.status_code == 400)

        info = await business_api.get_business()
        check("business details load", bool(info["name"]), info["name"])
        check("business details carry no WhatsApp credentials",
              not any(k.startswith("whatsapp") for k in info))

    finally:
        await cleanup([booked_id])


async def check_two_clinics():
    """
    A second clinic on the same server, driven through the real app as the
    dashboard and WhatsApp would: its own login, team, treatments,
    appointments and number, and none of the first clinic's.
    """
    print()
    print("=" * 70)
    print("TWO CLINICS ON ONE SERVER")
    print("=" * 70)

    import uuid
    import httpx
    from app.core import tenancy
    from app.core.models.appointment import Appointment
    from app.core.models.customer import Customer
    from app.core.timeutils import now as clinic_now
    from app.dependencies import issue_token
    from app.main import app
    from app.services import reminders

    if not settings.JWT_SECRET:
        check("two clinics: JWT_SECRET is set, to sign in", False, "run scripts/configure.py")
        return

    owner = {"Authorization": f"Bearer {issue_token()['access_token']}"}
    # A WhatsApp number id no real clinic has.
    number = "99" + str(uuid.uuid4().int)[:12]
    second = None

    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://test") as client:
            r = await client.post("/api/v1/platform/clinics", headers=owner, json={
                "name": "Integration Test Clinic", "phone": "0500000000",
                "whatsapp_phone_id": number,
                "working_hours_start": "08:00", "working_hours_end": "22:00"})
            check("the owner can add a clinic", r.status_code == 201, r.text[:150])
            if r.status_code != 201:
                return
            second = r.json()["id"]
            as_second = {**owner, "X-Business-Id": second}

            r = await client.post("/api/v1/platform/clinics", headers=owner, json={
                "name": "Same Number Clinic", "phone": "0500000001", "whatsapp_phone_id": number})
            check("no two clinics can share a WhatsApp number", r.status_code == 409, str(r.status_code))

            everyday = "Sunday,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday"
            r = await client.post("/api/v1/therapists", headers=as_second, json={
                "name": "Test Therapist B", "working_days": everyday,
                "working_hours_start": "08:00", "working_hours_end": "22:00"})
            check("the owner can give it a team", r.status_code == 201, r.text[:150])
            r = await client.post("/api/v1/treatments", headers=as_second, json={
                "name": "Test Treatment B", "duration_minutes": 30, "price": 100})
            check("and treatments", r.status_code == 201, r.text[:150])
            treatment_b = r.json()

            email = f"integration-{second[:8]}@example.com"
            r = await client.post(f"/api/v1/platform/clinics/{second}/logins", headers=owner,
                                  json={"email": email, "name": "Integration"})
            given = r.json().get("password", "") if r.status_code == 201 else ""
            check("and a user, with the start password or a random one to pass on",
                  given == settings.CLINIC_START_PASSWORD if settings.CLINIC_START_PASSWORD else len(given) >= 12,
                  str(r.status_code))
            login_id, password = r.json()["login"]["id"], given

            r = await client.post("/api/v1/auth/login", json={"email": email.upper(), "password": password})
            # The status only: the body holds a live token, which has no place in a log.
            check("the clinic signs in with its email, however it is typed", r.status_code == 200, str(r.status_code))
            clinic = {"Authorization": f"Bearer {r.json()['access_token']}"}

            me = (await client.get("/api/v1/auth/me", headers=clinic)).json()
            check("it is told which clinic it is", me.get("business_id") == second, str(me))
            check("and that it must choose its own password", me.get("must_change_password") is True, str(me))
            r = await client.get("/api/v1/therapists", headers=clinic)
            check("until it does, it sees nothing", r.status_code == 403, str(r.status_code))
            own = f"own-{uuid.uuid4().hex[:12]}"
            r = await client.post("/api/v1/auth/password", headers=clinic,
                                  json={"current_password": password, "new_password": password})
            check("keeping the given password is refused", r.status_code == 400, str(r.status_code))
            r = await client.post("/api/v1/auth/password", headers=clinic,
                                  json={"current_password": password, "new_password": own})
            me = (await client.get("/api/v1/auth/me", headers=clinic)).json()
            check("once it chooses one, it is in", r.status_code == 200 and me.get("must_change_password") is False,
                  str(r.status_code))

            team = [t["name"] for t in (await client.get("/api/v1/therapists", headers=clinic)).json()]
            check("a clinic sees only its own team", team == ["Test Therapist B"], str(team))
            offered = [t["name"] for t in (await client.get("/api/v1/treatments", headers=clinic)).json()]
            check("and only its own treatments", offered == ["Test Treatment B"], str(offered))

            day = (clinic_now().date() + timedelta(days=2)).isoformat()
            free = (await client.get("/api/v1/availability", headers=clinic,
                                     params={"treatment_id": treatment_b["id"], "date": day})).json()
            check("its free times come from its own team",
                  bool(free.get("slots")) and {s["therapist_name"] for s in free["slots"]} == {"Test Therapist B"},
                  f"{len(free.get('slots', []))} slots")
            slot = free["slots"][0]
            r = await client.post("/api/v1/appointments", headers=clinic, json={
                "customer_phone": TEST_PHONE, "treatment_name": "Test Treatment B",
                "therapist_name": slot["therapist_name"], "date": day, "time": slot["time"]})
            check("it books its own appointments", r.status_code == 201, r.text[:150])
            booked = r.json()["id"]

            home = (await client.get("/api/v1/appointments", headers=owner,
                                     params={"from_date": day, "to_date": day})).json()
            check("the first clinic does not see them", all(a["id"] != booked for a in home))
            r = await client.get(f"/api/v1/appointments/{booked}", headers=owner)
            check("nor open one by its id", r.status_code == 404, str(r.status_code))

            r = await client.get("/api/v1/appointments", headers={**clinic, "X-Business-Id": settings.BUSINESS_ID})
            check("a clinic login cannot ask for another clinic", r.status_code == 403, str(r.status_code))
            r = await client.get("/api/v1/platform/clinics", headers=clinic)
            check("nor reach the owner's pages", r.status_code == 403, str(r.status_code))

            found = await tenancy.business_for_phone_number(number)
            check("a WhatsApp message to its number finds it", found == second, str(found))
            with tenancy.acting_for(second):
                bot_offers = [t["name"] for t in await availability.get_treatments()]
            check("and the bot there offers its treatments only", bot_offers == ["Test Treatment B"], str(bot_offers))

            # Reminders go out from the clinic's own number. Run as if it were
            # twenty years on, so nothing real is in the window.
            sent = []

            async def fake_send(phone, text):
                line = tenancy.channel()
                sent.append((phone, line.phone_id if line else None))
                return True

            real_send = reminders.send_message
            reminders.send_message = fake_send
            try:
                later = clinic_now() + timedelta(days=7305)
                async with booking.async_session() as s:
                    customer = (await s.execute(select(Customer).where(
                        Customer.phone == TEST_PHONE, Customer.business_id == uuid.UUID(second)))).scalar_one()
                    s.add(Appointment(
                        id=uuid.uuid4(), business_id=uuid.UUID(second), customer_id=customer.id,
                        therapist_id=uuid.UUID(slot["therapist_id"]), treatment_id=uuid.UUID(treatment_b["id"]),
                        start_time=later + timedelta(hours=23), end_time=later + timedelta(hours=23, minutes=30),
                        status="confirmed", reminder_24h_sent=False, reminder_1h_sent=False))
                    await s.commit()
                await reminders.send_due_reminders(now=later)
            finally:
                reminders.send_message = real_send
            check("its reminders go out from its own number", sent == [(TEST_PHONE, number)], str(sent))

            await client.put(f"/api/v1/platform/logins/{login_id}", headers=owner, json={"is_active": False})
            r = await client.get("/api/v1/therapists", headers=clinic)
            check("a login switched off is locked out at once", r.status_code == 401, str(r.status_code))

            r = await client.put(f"/api/v1/platform/clinics/{second}", headers=owner, json={"is_active": False})
            found = await tenancy.business_for_phone_number(number)
            check("a clinic turned off answers no WhatsApp messages",
                  r.status_code == 200 and found is None and tenancy.channel(second) is None, str(found))

    finally:
        if second:
            await remove_clinic(second)
        await tenancy.refresh_channels()


async def remove_clinic(business_id):
    """Delete a test clinic and everything in it."""
    import uuid
    from sqlalchemy import delete
    from app.core.models.appointment import Appointment
    from app.core.models.customer import Customer
    from app.core.models.therapist import Therapist
    from app.core.models.treatment import Treatment
    from app.core.models.user import User

    key = uuid.UUID(business_id)
    async with booking.async_session() as s:
        for model in (Appointment, Customer, User, Therapist, Treatment):
            await s.execute(delete(model).where(model.business_id == key))
        await s.execute(delete(Business).where(Business.id == key))
        await s.commit()


async def cleanup(appointment_ids):
    """Remove everything this test created."""
    from app.core.models.appointment import Appointment
    from app.core.models.customer import Customer
    async with booking.async_session() as s:
        for aid in [a for a in appointment_ids if a]:
            obj = await s.get(Appointment, aid)
            if obj:
                await s.delete(obj)
        for phone in (TEST_PHONE, "972500000998"):
            res = await s.execute(select(Customer).where(Customer.phone == phone))
            for c in res.scalars().all():
                r2 = await s.execute(select(Appointment).where(Appointment.customer_id == c.id))
                for a in r2.scalars().all():
                    await s.delete(a)
                await s.delete(c)
        await s.commit()
    print("\n(cleaned up test rows)")


def summarize():
    print()
    print("=" * 70)
    passed = sum(1 for r in results if r[0] == PASS)
    failed = sum(1 for r in results if r[0] == FAIL)
    print(f"RESULT: {passed} passed, {failed} failed")
    if failed:
        print("\nFailures:")
        for status, name, detail in results:
            if status == FAIL:
                print(f"  - {name}: {detail}")
    print("=" * 70)
    return failed


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)

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
    print(f"hardcoded BUSINESS_ID: {availability.BUSINESS_ID}")
    check(
        "hardcoded BUSINESS_ID matches a real business row",
        availability.BUSINESS_ID in db_ids,
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

    return summarize()


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

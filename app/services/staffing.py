"""
Who does which treatment.

A therapist either does every treatment, or only the ones ticked for them.
Every therapist starts out doing all of them (no rows in
therapist_treatments), so a team nobody has set up keeps working as before:
free times, the bot and the dashboard only narrow once a clinic ticks
treatments for someone.
"""
import uuid
from collections import defaultdict

from sqlalchemy import delete, insert, select

from app.core.models.treatment import therapist_treatments


def can_do(ticked: set | None, treatment_id) -> bool:
    """Whether someone with these treatments ticked does this one. None ticked: all of them."""
    if not ticked:
        return True
    key = treatment_id if isinstance(treatment_id, uuid.UUID) else uuid.UUID(str(treatment_id))
    return key in ticked


async def ticked_treatments(session, therapist_ids) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The treatments ticked for each of these therapists. Missing: they do all of them."""
    ids = list(therapist_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(therapist_treatments.c.therapist_id, therapist_treatments.c.treatment_id)
        .where(therapist_treatments.c.therapist_id.in_(ids))
    )
    ticked = defaultdict(set)
    for therapist_id, treatment_id in rows:
        ticked[therapist_id].add(treatment_id)
    return dict(ticked)


async def who_does(session, therapists, treatment_id) -> list:
    """Those of these therapists who do the treatment, in the same order."""
    ticked = await ticked_treatments(session, [t.id for t in therapists])
    return [t for t in therapists if can_do(ticked.get(t.id), treatment_id)]


async def set_treatments(session, therapist_id, treatment_ids) -> None:
    """
    Replace the treatments ticked for a therapist. Empty: all of them.
    The ids must already be checked to be the same clinic's.
    """
    await session.execute(
        delete(therapist_treatments).where(therapist_treatments.c.therapist_id == therapist_id))
    if treatment_ids:
        await session.execute(insert(therapist_treatments), [
            {"therapist_id": therapist_id, "treatment_id": treatment_id}
            for treatment_id in treatment_ids
        ])

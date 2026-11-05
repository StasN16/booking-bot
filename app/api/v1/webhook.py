import hashlib
import hmac
import json
import logging
from collections import OrderedDict

from fastapi import APIRouter, BackgroundTasks, Request, HTTPException, Query
from app.config import settings
from app.core import tenancy
from app.services.conversation import handle_message

logger = logging.getLogger(__name__)
router = APIRouter()

# Meta retries a webhook until it gets a 2xx, and retries can overlap with a
# slow first attempt. Without this, one customer message books twice.
MAX_REMEMBERED_MESSAGES = 1000
_seen_message_ids = OrderedDict()


def signed_by_meta(body: bytes, signature: str | None) -> bool:
    """
    Whether Meta sent this body: it signs every webhook with the app's
    secret (X-Hub-Signature-256). Online the address is public, and without
    this anyone could post a made-up message and have the bot answer it,
    from the clinic's number, to a number of their choosing.

    With no secret set there is nothing to check against, as on a machine
    used for development; the server warns about it when it starts.
    """
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        return True
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature.removeprefix("sha256="), expected)


def already_processed(message_id: str) -> bool:
    """Record a message id, reporting whether it had already been seen."""
    if not message_id:
        return False
    if message_id in _seen_message_ids:
        return True
    _seen_message_ids[message_id] = True
    while len(_seen_message_ids) > MAX_REMEMBERED_MESSAGES:
        _seen_message_ids.popitem(last=False)
    return False


def extract_messages(body: dict) -> list:
    """
    Pull the text messages out of a webhook payload, ignoring the rest.

    Each one says which WhatsApp number it was sent to ("to", Meta's phone
    number id), which is how it finds its clinic.
    """
    messages = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            to = (value.get("metadata") or {}).get("phone_number_id")
            for message in value.get("messages") or []:
                if message.get("type") and message.get("type") != "text":
                    continue
                text = (message.get("text") or {}).get("body", "")
                from_number = message.get("from")
                if text and from_number:
                    messages.append({
                        "id": message.get("id"),
                        "from": from_number,
                        "text": text,
                        "to": to,
                    })
    return messages


async def handle_message_for(business_id: str, from_number: str, text: str):
    """Answer a message as the clinic it was sent to."""
    with tenancy.acting_for(business_id):
        await handle_message(from_number, text)


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge")
):
    """Meta calls this to verify your webhook URL is real"""
    expected = settings.WHATSAPP_VERIFY_TOKEN
    if (hub_mode == "subscribe" and expected and hub_verify_token
            and hmac.compare_digest(hub_verify_token, expected)
            and (hub_challenge or "").isdigit()):
        logger.info("Webhook verified successfully")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_message(request: Request, background_tasks: BackgroundTasks):
    """Meta calls this when a WhatsApp message is received"""
    raw = await request.body()
    if not signed_by_meta(raw, request.headers.get("X-Hub-Signature-256")):
        # Not from Meta, or WHATSAPP_APP_SECRET is not this app's secret.
        logger.warning("Webhook refused: not signed with WHATSAPP_APP_SECRET")
        raise HTTPException(status_code=403, detail="Bad signature")
    try:
        body = json.loads(raw)
    except Exception:
        logger.warning("Webhook received a body that was not valid JSON")
        return {"status": "ok"}

    try:
        for message in extract_messages(body):
            if already_processed(message["id"]):
                logger.info(f"Skipping duplicate delivery of message {message['id']}")
                continue

            business_id = await tenancy.business_for_phone_number(message["to"])
            if business_id is None:
                # A number no clinic has: nobody to answer for.
                logger.warning(f"Message to WhatsApp number {message['to']}, "
                               f"which no active clinic has; ignored")
                continue

            logger.info(f"Message from {message['from']}: {message['text']}")
            # Replying takes several seconds; answer Meta now and work after.
            background_tasks.add_task(handle_message_for, business_id,
                                      message["from"], message["text"])

    except Exception as e:
        logger.exception(f"Error processing webhook: {e}")

    # Always 200 - a non-2xx makes Meta redeliver the same message.
    return {"status": "ok"}

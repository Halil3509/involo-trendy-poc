"""Instagram webhook verification, audit logging, and event dispatch."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from app.core.config import Settings
from app.infrastructure.resources import utcnow
from app.tasks import profile_user


class InstagramWebhookService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def verify_signature(self, body: bytes, signature: str | None) -> bool:
        secret = self.settings.instagram_app_secret
        if not secret or not signature:
            return False
        expected = "sha256=" + hmac.new(
            secret.get_secret_value().encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def handle_event(self, db: Any, body: bytes) -> None:
        payload = json.loads(body)
        now = utcnow()

        await db.instagram_webhook_events.insert_one(
            {
                "received_at": now,
                "object": payload.get("object"),
                "payload": payload,
            }
        )

        for entry in payload.get("entry", []):
            account_id = str(entry.get("id", ""))
            if not account_id:
                continue

            connection = await db.instagram_connections.find_one(
                {"instagram_user_id": account_id}
            )
            if not connection:
                continue

            user_id = connection["user_id"]
            await db.instagram_connections.update_one(
                {"_id": connection["_id"]},
                {
                    "$set": {
                        "status": "profiling",
                        "error": None,
                        "profiling_queued_at": now,
                    }
                },
            )
            profile_user.apply_async(args=[str(user_id)])

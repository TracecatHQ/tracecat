"""The SNS subscription-confirmation window, apart from the notification cap."""

from __future__ import annotations

import uuid

from tracecat.triggers.ingest.rate_limit import count_in_window

# A genuine source sees a confirmation once per subscribe. Confirmations get
# their own small window so replayed ones never use the notification budget.
CONFIRMATIONS_PER_MINUTE = 10


async def check_confirmation_rate(source_id: uuid.UUID) -> int | None:
    """Count one subscription confirmation against the source's own small cap.

    Returns:
        ``None`` if within the cap, else the seconds until the window resets.
    """
    return await count_in_window(
        source_id, counter="confirm", limit=CONFIRMATIONS_PER_MINUTE
    )

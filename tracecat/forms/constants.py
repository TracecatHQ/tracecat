"""Limits and fixed values for forms."""

from datetime import timedelta

MAX_IMAGE_BYTES = 100 * 1024
"""Decoded size limit for one inline image."""

MAX_CONFIG_IMAGE_BYTES = 1024 * 1024
"""Decoded size limit for all inline images in one form config."""

MAX_FILES_PER_FIELD = 10

MAX_PENDING_FILES_PER_UPLOADER = 20
"""Uploaded files one caller may hold on one form before submitting them."""

PENDING_FILE_TTL = timedelta(hours=24)
"""How long an uploaded file may wait for a submission to claim it."""

EXPIRED_FILE_PURGE_BATCH = 100
"""Expired files removed by one upload or submit."""

FORM_VIEW_EVENT_DEDUP_WINDOW = timedelta(minutes=5)

SUBMISSION_REFERENCE_PREFIX = "FRM-"

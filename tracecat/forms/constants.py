"""Limits and fixed values for forms."""

from datetime import timedelta

MAX_IMAGE_BYTES = 100 * 1024
"""Decoded size limit for one inline image."""

MAX_CONFIG_IMAGE_BYTES = 1024 * 1024
"""Decoded size limit for all inline images in one form config."""

MAX_SHORT_TEXT_LENGTH = 255
"""Titles, labels, placeholders, input names and option values."""

MAX_LONG_TEXT_LENGTH = 10_000
"""Descriptions, help text, messages and markdown blocks."""

MAX_LAYOUT_ENTRIES = 200

MAX_FIELD_OPTIONS = 100

MAX_ACCEPT_ENTRIES = 20

MAX_FILES_PER_FIELD = 10

MAX_PENDING_FILES_PER_UPLOADER = 20
"""Uploaded files one caller may hold on one form before submitting them."""

PENDING_FILE_TTL = timedelta(hours=24)
"""How long an uploaded file may wait for a submission to claim it."""

EXPIRED_FILE_PURGE_BATCH = 100
"""Expired files removed by one upload."""

MAX_BLOB_DELETES_PER_REQUEST = 500
"""Blobs one request deletes. The rest stay in storage and are logged."""

FORM_VIEW_DEDUP_WINDOW = timedelta(minutes=5)
"""A user's repeat views of a form within this window count once."""

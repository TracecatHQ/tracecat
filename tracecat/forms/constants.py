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

MAX_SAFE_INTEGER = 2**53 - 1
"""Largest whole number a browser holds exactly. Forms take integers within
plus or minus this, in defaults, ``visible_if`` values and answers."""

MAX_PENDING_FILES_PER_UPLOADER = 20
"""Uploaded files one caller may hold on one form before submitting them."""

PENDING_FILE_TTL = timedelta(hours=24)
"""How long an uploaded file may wait for a submission to claim it."""

EXPIRED_FILE_PURGE_BATCH = 100
"""Expired files removed in one transaction."""

EXPIRED_FILE_PURGE_INTERVAL = timedelta(minutes=10)
"""How often each API process purges expired files."""

BLOB_DELETE_BATCH = 50
"""Blobs deleted at once. Every blob is deleted, one batch after another."""

FORM_VIEW_DEDUP_WINDOW = timedelta(minutes=5)
"""A user's views of a form in one window of this length count once.

Windows are fixed, aligned to the Unix epoch."""

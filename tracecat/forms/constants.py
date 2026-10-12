"""Limits and fixed values for forms."""

from datetime import timedelta

MAX_IMAGE_BYTES = 100 * 1024
"""Decoded size limit for one inline image."""

MAX_CONFIG_IMAGE_BYTES = 1024 * 1024
"""Decoded size limit for all inline images in one form config."""

MAX_FILES_PER_FIELD = 10

FORM_VIEW_EVENT_DEDUP_WINDOW = timedelta(minutes=5)

SUBMISSION_REFERENCE_PREFIX = "FRM-"

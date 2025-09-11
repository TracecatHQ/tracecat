import re

GIT_SSH_URL_REGEX = re.compile(
    r"^git\+ssh://git@(?P<host>[^/]+)/(?P<path>[^@]+?)(?:\.git)?(?:@(?P<ref>[^/@]+))?$"
)
"""Git SSH URL with git user and optional ref. Supports nested groups and ports."""

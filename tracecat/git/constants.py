import re

GIT_SSH_URL_REGEX = re.compile(
    r"^git\+ssh://(?P<user>[^/@:]+)@(?P<host>[^/:]+)(?::(?P<port>\d+))?/(?P<path>[^/@]+?(?:/[^/@]+?)+?)(?:\.git)?(?:@(?P<ref>[^@]+))?$"
)
"""Git SSH URL with arbitrary SSH user, optional numeric port, and multi-segment paths."""

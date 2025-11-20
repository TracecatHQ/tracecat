import re

GIT_SSH_URL_REGEX = re.compile(
    r"^git\+ssh://git@(?P<host>[^/:]+)(?::(?P<port>\d+))?/(?P<path>[^/@]+?(?:/[^/@]+?)+?)(?:\.git)?(?:@(?P<ref>[^/@]+))?$"
)
"""Git SSH URL with git user, optional numeric port, and multi-segment paths. Uses lazy quantifiers to exclude .git from path capture."""

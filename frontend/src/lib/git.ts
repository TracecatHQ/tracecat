export const GIT_SSH_URL_REGEX =
  /^git\+ssh:\/\/git@(?<hostname>[^/:]+)(?::(?<port>\d+))?\/(?<path>[^/@]+(?:\/[^/@]+)+)(?:\.git)?(?:@(?<ref>[^/@]+))?$/

// Mirrors the backend validation in tracecat/git/constants.py but enforces at least
// an <org>/<repo> path structure on the client.

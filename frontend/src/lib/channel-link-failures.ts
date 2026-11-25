export type LinkFailure = { message: string; retryable: boolean }

// The callback redirects with a code, never text, so the page shows only this copy.
// A link is spent only by a matching sign-in, so retryable failures can reuse it.
export const LINK_FAILURES: Record<string, LinkFailure> = {
  invalid: {
    message: "This link is invalid, expired, or already used.",
    retryable: false,
  },
  not_member: {
    message: "Sign in as a member of the organization that sent this link.",
    retryable: false,
  },
  other_user: {
    message: "Sign in as the Tracecat user who started linking.",
    retryable: true,
  },
  wrong_account: {
    message:
      "You signed in to the provider as a different user than the one who asked to link.",
    retryable: true,
  },
  cancelled: { message: "Sign-in was cancelled.", retryable: true },
  signed_out: {
    message: "Your Tracecat session ended. Sign in again to retry.",
    retryable: true,
  },
  failed: {
    message: "The provider could not confirm your sign-in.",
    retryable: true,
  },
  no_bot: {
    message:
      "Your organization has no active Tracecat bot in that Slack workspace.",
    retryable: false,
  },
  blocked_network: {
    message:
      "Your organization's IP allowlist does not include this network. Try again from an allowed network.",
    retryable: true,
  },
}

/** The fixed copy for a callback error code; unknown codes read as invalid. */
export function linkFailureMessage(code: string): string {
  const failure = Object.hasOwn(LINK_FAILURES, code)
    ? LINK_FAILURES[code]
    : LINK_FAILURES.invalid
  return failure.message
}

import { getApiErrorDetail } from "@/lib/errors"

interface WorkspaceSyncPushErrorNotice {
  title: string
  description: string
  isDestructive: boolean
}

/**
 * Describe a failed push without claiming failure when the gateway timed out
 * before the synchronous export finished.
 */
export function getWorkspaceSyncPushErrorNotice(
  error: unknown
): WorkspaceSyncPushErrorNotice {
  if (getErrorStatus(error) === 504) {
    return {
      title: "Push is taking longer than expected",
      description:
        "The request timed out, but the push may still complete. Check the target branch before trying again.",
      isDestructive: false,
    }
  }

  return {
    title: "Push failed",
    description: getApiErrorDetail(error) ?? "Request failed",
    isDestructive: true,
  }
}

function getErrorStatus(error: unknown): number | null {
  if (typeof error !== "object" || error === null || !("status" in error)) {
    return null
  }
  return typeof error.status === "number" ? error.status : null
}

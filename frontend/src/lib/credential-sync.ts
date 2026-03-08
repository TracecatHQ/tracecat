import type { CredentialSyncResult } from "@/client"

export function formatCredentialSyncResultSummary(
  result: CredentialSyncResult
): string {
  const parts = [
    `${result.processed ?? 0} processed`,
    `${result.created ?? 0} created`,
    `${result.updated ?? 0} updated`,
  ]
  if ((result.failed ?? 0) > 0) {
    parts.push(`${result.failed} failed`)
  }
  return parts.join(" • ")
}

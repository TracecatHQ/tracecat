export const MAX_APPROVAL_BADGE_COUNT = 99

export function formatPendingApprovalCount(count: number): string {
  if (count > MAX_APPROVAL_BADGE_COUNT) {
    return `${MAX_APPROVAL_BADGE_COUNT}+`
  }

  return count.toString()
}

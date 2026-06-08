export const MAX_INBOX_BADGE_COUNT = 99

export function formatPendingInboxCount(count: number): string {
  if (count > MAX_INBOX_BADGE_COUNT) {
    return `${MAX_INBOX_BADGE_COUNT}+`
  }

  return count.toString()
}

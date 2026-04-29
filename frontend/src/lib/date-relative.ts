export function getRelativeDateLabel(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return "0m"
  }

  const diffMs = Math.max(0, Date.now() - timestamp)
  const minuteMs = 60_000
  const hourMs = 60 * minuteMs
  const dayMs = 24 * hourMs
  const monthMs = 30 * dayMs
  const yearMs = 365 * dayMs

  if (diffMs < hourMs) {
    return `${Math.max(1, Math.floor(diffMs / minuteMs))}m`
  }
  if (diffMs < dayMs) {
    return `${Math.max(1, Math.floor(diffMs / hourMs))}hr`
  }
  if (diffMs < monthMs) {
    return `${Math.max(1, Math.floor(diffMs / dayMs))}d`
  }
  if (diffMs < yearMs) {
    return `${Math.max(1, Math.floor(diffMs / monthMs))}mo`
  }
  return `${Math.max(1, Math.floor(diffMs / yearMs))}y`
}

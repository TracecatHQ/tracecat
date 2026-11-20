/**
 * Format a case count for display. A capped count is a lower bound: the
 * server stopped counting there, so it reads "10,000+".
 */
export function formatCaseCount(count: number, capped: boolean): string {
  return capped ? `${count.toLocaleString()}+` : String(count)
}

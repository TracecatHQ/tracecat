export function getTokensCacheKey(code: string, language: string): string {
  return `${language}\u0000${code}`
}

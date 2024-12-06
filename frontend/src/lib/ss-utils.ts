export function buildUrl(path: string) {
  const url = process.env.NEXT_SERVER_API_URL || "http://api:8000"
  if (path.startsWith("/")) {
    return `${url}${path}`
  }
  return `${url}/${path}`
}

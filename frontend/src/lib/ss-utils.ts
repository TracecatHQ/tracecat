import { NextRequest } from "next/server"

export const getDomain = (request: NextRequest) => {
  // use env variable if set
  if (process.env.NEXT_PUBLIC_APP_URL) {
    console.log(
      "Redirecting to NEXT_PUBLIC_APP_URL:",
      process.env.NEXT_PUBLIC_APP_URL
    )
    return process.env.NEXT_PUBLIC_APP_URL
  }

  // next, try and build domain from headers
  const requestedHost = request.headers.get("X-Forwarded-Host")
  const requestedPort = request.headers.get("X-Forwarded-Port")
  const requestedProto = request.headers.get("X-Forwarded-Proto")
  if (requestedHost) {
    const url = request.nextUrl.clone()
    url.host = requestedHost
    url.protocol = requestedProto || url.protocol
    url.port = requestedPort || url.port
    console.log("Redirecting to requestedHost", url.origin)
    return url.origin
  }

  // finally just use whatever is in the request
  return request.nextUrl.origin
}

export function buildUrl(path: string) {
  const url = process.env.NEXT_SERVER_API_URL || "http://api:8000"
  if (path.startsWith("/")) {
    return `${url}${path}`
  }
  return `${url}/${path}`
}

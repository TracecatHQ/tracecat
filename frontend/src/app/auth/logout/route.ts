import { type NextRequest, NextResponse } from "next/server"
import {
  POST_AUTH_RETURN_URL_COOKIE_NAME,
  sanitizeReturnUrl,
} from "@/lib/auth-return-url"
import { buildUrl } from "@/lib/ss-utils"

const AUTH_COOKIE_NAME_PREFIXES = ["tracecat_auth_", "__pa_", "_pa_", "pa_"]

const AUTH_COOKIE_NAME_SUBSTRINGS = [
  "propelauth",
  "magiclink",
  "magic_link",
  "magic-link",
]

function getConfiguredCookieNames(): Set<string> {
  const raw = process.env.TRACECAT__AUTH_LOGOUT_CLEAR_COOKIES ?? ""
  const names = raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0)
  return new Set(names)
}

function isIpAddress(hostname: string): boolean {
  return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(hostname)
}

function getDomainVariants(hostname: string): string[] {
  if (!hostname || hostname === "localhost" || isIpAddress(hostname)) {
    return []
  }
  return [hostname, `.${hostname}`]
}

function shouldClearCookieName(
  name: string,
  configuredCookieNamesLowercase: Set<string>
): boolean {
  const lowerName = name.toLowerCase()
  return (
    configuredCookieNamesLowercase.has(lowerName) ||
    lowerName === "fastapiusersauth" ||
    AUTH_COOKIE_NAME_PREFIXES.some((prefix) => lowerName.startsWith(prefix)) ||
    AUTH_COOKIE_NAME_SUBSTRINGS.some((term) => lowerName.includes(term))
  )
}

function clearCookie(
  response: NextResponse,
  name: string,
  hostname: string
): void {
  response.cookies.set({
    name,
    value: "",
    maxAge: 0,
    path: "/",
  })
  for (const domain of getDomainVariants(hostname)) {
    response.cookies.set({
      name,
      value: "",
      maxAge: 0,
      path: "/",
      domain,
    })
  }
}

function clearAuthCookies(request: NextRequest, response: NextResponse): void {
  const configuredCookieNames = getConfiguredCookieNames()
  const configuredCookieNamesLowercase = new Set(
    Array.from(configuredCookieNames, (name) => name.toLowerCase())
  )

  const cookieNamesToClear = new Set<string>([
    POST_AUTH_RETURN_URL_COOKIE_NAME,
    ...configuredCookieNames,
  ])

  for (const { name } of request.cookies.getAll()) {
    if (shouldClearCookieName(name, configuredCookieNamesLowercase)) {
      cookieNamesToClear.add(name)
    }
  }

  for (const cookieName of cookieNamesToClear) {
    clearCookie(response, cookieName, request.nextUrl.hostname)
  }
}

function getBackendSetCookieHeaders(headers: Headers): string[] {
  const headersWithSetCookie = headers as Headers & {
    getSetCookie?: () => string[]
  }
  const setCookieHeaders = headersWithSetCookie.getSetCookie?.()
  if (setCookieHeaders && setCookieHeaders.length > 0) {
    return setCookieHeaders
  }

  const setCookieHeader = headers.get("set-cookie")
  return setCookieHeader ? [setCookieHeader] : []
}

async function appendBackendLogoutCookies(
  request: NextRequest,
  response: NextResponse
): Promise<void> {
  const incomingCookie = request.headers.get("cookie")

  try {
    const backendResponse = await fetch(buildUrl("/auth/logout"), {
      method: "POST",
      headers: incomingCookie ? { cookie: incomingCookie } : undefined,
      cache: "no-store",
    })

    if (!backendResponse.ok) {
      console.warn(
        `Backend logout failed with status ${backendResponse.status}`
      )
      return
    }

    for (const setCookie of getBackendSetCookieHeaders(
      backendResponse.headers
    )) {
      response.headers.append("set-cookie", setCookie)
    }
  } catch (error) {
    console.warn("Backend logout request failed", error)
  }
}

export async function POST(request: NextRequest): Promise<NextResponse> {
  const response = new NextResponse(null, { status: 204 })
  await appendBackendLogoutCookies(request, response)
  clearAuthCookies(request, response)
  return response
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const redirectTarget =
    sanitizeReturnUrl(request.nextUrl.searchParams.get("redirectTo")) ??
    "/sign-in"
  const response = NextResponse.redirect(
    new URL(redirectTarget, request.nextUrl.origin)
  )
  await appendBackendLogoutCookies(request, response)
  clearAuthCookies(request, response)
  return response
}

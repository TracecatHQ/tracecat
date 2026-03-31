import { type NextRequest, NextResponse } from "next/server"

import {
  decodeAndSanitizeReturnUrl,
  POST_AUTH_RETURN_URL_COOKIE_NAME,
  serializeClearPostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"
import { buildUrl } from "@/lib/ss-utils"

/**
 * @param request
 * @returns
 */
export async function POST(request: NextRequest) {
  const returnUrl = decodeAndSanitizeReturnUrl(
    request.cookies.get(POST_AUTH_RETURN_URL_COOKIE_NAME)?.value
  )

  // Parse the form data from the request
  const formData = await request.formData()
  const samlResponse = formData.get("SAMLResponse")
  const relayState = formData.get("RelayState")

  // Get redirect
  const resp = await fetch(buildUrl("/info"))
  const { public_app_url } = await resp.json()

  if (!samlResponse) {
    console.error("No SAML response found in the request")
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  // Prepare the request to the FastAPI backend
  const backendUrl = new URL(buildUrl("/auth/saml/acs"))
  const backendFormData = new FormData()
  backendFormData.append("SAMLResponse", samlResponse)

  // Forward RelayState if present
  if (relayState) {
    backendFormData.append("RelayState", relayState)
  }

  // Forward the request to the FastAPI backend
  const headers = {
    "x-tracecat-service-key": process.env.TRACECAT__SERVICE_KEY!,
    "x-tracecat-role-type": "service",
    "x-tracecat-role-service-id": "tracecat-ui",
  }
  const backendResponse = await fetch(backendUrl.toString(), {
    method: "POST",
    body: backendFormData,
    headers: headers,
  })

  const setCookieHeader = backendResponse.headers.get("set-cookie")
  const contentType = backendResponse.headers.get("content-type")

  if (!setCookieHeader && contentType?.startsWith("text/html")) {
    const response = new NextResponse(await backendResponse.text(), {
      status: backendResponse.status,
      headers: {
        "content-type": contentType,
      },
    })
    response.headers.append(
      "set-cookie",
      serializeClearPostAuthReturnUrlCookie(
        request.nextUrl.protocol === "https:"
      )
    )
    return response
  }

  if (!backendResponse.ok) {
    console.error("Error from backend:", await backendResponse.text())
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  const targetPath = returnUrl ?? "/"
  const redirectUrl = new URL(targetPath, public_app_url)
  const redirectResponse = NextResponse.redirect(redirectUrl, {
    status: 303, // Force GET request
  })
  redirectResponse.headers.append("set-cookie", setCookieHeader)
  redirectResponse.headers.append(
    "set-cookie",
    serializeClearPostAuthReturnUrlCookie(request.nextUrl.protocol === "https:")
  )
  return redirectResponse
}

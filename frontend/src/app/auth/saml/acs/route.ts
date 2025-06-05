import { NextRequest, NextResponse } from "next/server"

import { buildUrl } from "@/lib/ss-utils"

/**
 * @param request
 * @returns
 */
export async function POST(request: NextRequest) {
  console.log("POST /auth/saml/acs", request.nextUrl.toString())

  // Parse the form data from the request
  const formData = await request.formData()
  const samlResponse = formData.get("SAMLResponse")
  const relayState = formData.get("RelayState")

  // Get redirect
  const resp = await fetch(buildUrl("/info"))
  const { public_app_url } = await resp.json()
  console.log("Public app URL", public_app_url)

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
    'x-tracecat-service-key': process.env.TRACECAT__SERVICE_KEY!,
    'x-tracecat-role-type': 'service',
    'x-tracecat-role-service-id': 'tracecat-ui',
    'x-tracecat-role-access-level': 'BASIC',
  }
  console.log("Headers", headers)
  const backendResponse = await fetch(backendUrl.toString(), {
    method: "POST",
    body: backendFormData,
    headers: headers,
  })

  if (!backendResponse.ok) {
    console.error("Error from backend:", await backendResponse.text())
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  const setCookieHeader = backendResponse.headers.get("set-cookie")

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", public_app_url))
  }

  console.log("Redirecting to / with GET")
  const redirectUrl = new URL("/", public_app_url)
  const redirectResponse = NextResponse.redirect(redirectUrl, {
    status: 303, // Force GET request
  })
  redirectResponse.headers.set("set-cookie", setCookieHeader)
  return redirectResponse
}

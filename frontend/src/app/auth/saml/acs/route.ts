import { NextRequest, NextResponse } from "next/server"

import { buildUrl, getDomain } from "@/lib/ss-utils"

/**
 * @param request
 * @returns
 */
export async function POST(request: NextRequest) {
  console.log("POST /auth/saml/acs", request.nextUrl.toString())

  // Parse the form data from the request
  const formData = await request.formData()
  const samlResponse = formData.get('SAMLResponse')

  if (!samlResponse) {
    console.error("No SAML response found in the request")
    return NextResponse.redirect(new URL("/auth/error", getDomain(request)))
  }

  // Prepare the request to the FastAPI backend
  const backendUrl = new URL(buildUrl("/auth/saml/acs"))
  const backendFormData = new FormData()
  backendFormData.append('SAMLResponse', samlResponse)

  // Forward the request to the FastAPI backend
  const backendResponse = await fetch(backendUrl.toString(), {
    method: 'POST',
    body: backendFormData,
  })

  if (!backendResponse.ok) {
    console.error("Error from backend:", await backendResponse.text())
    return NextResponse.redirect(new URL("/auth/error", getDomain(request)))
  }

  const setCookieHeader = backendResponse.headers.get("set-cookie")

  if (!setCookieHeader) {
    console.error("No set-cookie header found in response")
    return NextResponse.redirect(new URL("/auth/error", getDomain(request)))
  }

  console.log("Redirecting to / with GET")
  const redirectUrl = new URL("/", getDomain(request))
  const redirectResponse = NextResponse.redirect(redirectUrl, {
    status: 303 // Force GET request
  })
  redirectResponse.headers.set("set-cookie", setCookieHeader)
  return redirectResponse
}

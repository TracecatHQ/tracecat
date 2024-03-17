import { redirect } from "next/navigation"
import { NextResponse } from "next/server"
import { createClient } from "@/utils/supabase/server"

import { createWorkflow } from "@/lib/flow"

export async function GET(request: Request) {
  // The `/auth/callback` route is required for the server-side auth flow implemented
  // by the SSR package. It exchanges an auth code for the user's session.
  // https://supabase.com/docs/guides/auth/server-side/nextjs
  const requestUrl = new URL(request.url)
  const code = requestUrl.searchParams.get("code")
  const origin = requestUrl.origin

  const supabase = createClient()
  if (code) {
    await supabase.auth.exchangeCodeForSession(code)
  }

  // Attempt to create a new user if one does not exist
  const {
    data: { session },
  } = await supabase.auth.getSession()

  // At this point session should be valid, but we'll check just in case
  if (!session) {
    console.error("Failed to get session")
    return redirect("/?level=error&message=Could not authenticate user")
  }
  const response = await fetch(`${process.env.NEXT_PUBLIC_APP_URL}/users`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${session.access_token}`,
    },
  })

  // If the user already exists, we'll get a 409 conflict
  if (!response.ok && response.status !== 409) {
    console.error("Failed to create user")
    return redirect("/?level=error&message=Could not authenticate user")
  }

  if (response.status !== 409) {
    console.log("New user created")
    await createWorkflow(
      session,
      "My first workflow",
      "Welcome to Tracecat. This is your first workflow!"
    )
    console.log("Created first workflow for new user")
  }
  // URL to redirect to after sign up process completes
  return NextResponse.redirect(`${origin}/workflows`)
}

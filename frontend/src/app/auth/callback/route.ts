import { redirect } from "next/navigation"
import { NextResponse } from "next/server"
import { createClient } from "@/utils/supabase/server"

import { safeNewUserFlow } from "@/lib/auth"

export async function GET(request: Request) {
  // The `/auth/callback` route is required for the server-side auth flow implemented
  // by the SSR package. It exchanges an auth code for the user's session.
  // https://supabase.com/docs/guides/auth/server-side/nextjs
  const requestUrl = new URL(request.url)
  const code = requestUrl.searchParams.get("code")
  const origin = requestUrl.origin

  const supabase = createClient()
  if (code) {
    console.log("Exchanging code for session")
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
  // If we are here, it means there's a valid session
  await safeNewUserFlow(session)
  // URL to redirect to after sign up process completes
  return NextResponse.redirect(`${origin}/workflows`)
}

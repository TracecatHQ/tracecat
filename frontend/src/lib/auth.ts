"use server"

import { headers } from "next/headers"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"
import { AuthError, Session } from "@supabase/supabase-js"

import { getAuthenticatedClient } from "@/lib/api"
import { createWorkflow } from "@/lib/flow"

type ThirdPartyAuthProvider = "google" | "github"
const EXPECTED_ERR_MSG_USER_EXISTS = "Request failed with status code 409"

/**
 * This sign-in flow is only used during local deployment.
 */
export async function signInFlow(formData: FormData) {
  const email = formData.get("email") as string
  const password = formData.get("password") as string
  const supabase = createClient()

  const {
    data: { session },
    error,
  } = await supabase.auth.signInWithPassword({
    email,
    password,
  })

  if (error || !session) {
    console.error("error", error, "session", session)
    return redirect("/?level=error&message=Could not authenticate user")
  }

  // If we are here, it means there's a valid session
  try {
    await newUserFlow(session)
  } catch (error) {
    const authError = error as AuthError
    if (authError.message.includes(EXPECTED_ERR_MSG_USER_EXISTS)) {
      console.log("User already exists, nothing to do here.")
    } else {
      console.error("Error creating user:", authError.message)
      return redirect("/?level=error&message=Error occured when creating user")
    }
  }
}

export async function signOutFlow() {
  const supabase = createClient()
  await supabase.auth.signOut()
  return redirect("/")
}

export async function signUpFlow(formData: FormData) {
  const origin = headers().get("origin")
  const email = formData.get("email") as string
  const password = formData.get("password") as string
  const supabase = createClient()

  const { error } = await supabase.auth.signUp({
    email,
    password,
    options: {
      emailRedirectTo: `${origin}/auth/callback`,
    },
  })

  if (error) {
    return redirect("/?level=error&message=Could not authenticate user")
  }

  return redirect("/?message=Check your email to continue")
}

export async function thirdPartyAuthFlow(provider: ThirdPartyAuthProvider) {
  const origin = headers().get("origin")
  const supabase = createClient()
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider,
    options: {
      queryParams: {
        access_type: "offline",
        prompt: "consent",
      },
      redirectTo: `${origin}/auth/callback`,
    },
  })

  if (error) {
    return redirect("/?level=error&message=Could not authenticate user")
  }
  return redirect(data.url)
}

export async function signInWithEmailMagicLink(formData: FormData) {
  const origin = headers().get("origin")
  const email = formData.get("email") as string
  const supabase = createClient()
  const { data, error } = await supabase.auth.signInWithOtp({
    email,
    options: {
      emailRedirectTo: `${origin}/auth/callback`,
    },
  })
  console.log(data, error)

  if (error) {
    return redirect("/?level=error&message=Could not authenticate user")
  }
  return redirect("/?message=Check your email to continue")
}

export async function newUserFlow(session: Session) {
  const client = getAuthenticatedClient(session)

  const response = await client.put("/users")

  switch (response.status) {
    case 409:
      console.log("User already exists")
      break
    case 201:
      console.log("New user created")
      await createWorkflow(
        session,
        "My first workflow",
        "Welcome to Tracecat. This is your first workflow!"
      )
      console.log("Created first workflow for new user")
  }
}

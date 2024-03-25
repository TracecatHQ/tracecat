"use server"

import { headers } from "next/headers"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"
import { Session } from "@supabase/supabase-js"

import { createWorkflow } from "@/lib/flow"

type ThirdPartyAuthProvider = "google" | "github"

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

  await newUserFlow(session)

  return redirect("/workflows")
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
}

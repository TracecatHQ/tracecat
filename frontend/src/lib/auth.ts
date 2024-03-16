"use server"

import { headers } from "next/headers"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"

type ThirdPartyAuthProvider = "google" | "github"

export async function signInFlow(formData: FormData) {
  const email = formData.get("email") as string
  const password = formData.get("password") as string
  const supabase = createClient()

  const { error } = await supabase.auth.signInWithPassword({
    email,
    password,
  })

  if (error) {
    return redirect("/login?message=Could not authenticate user")
  }

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
    return redirect("/login?message=Could not authenticate user")
  }

  return redirect("/login?message=Check email to continue sign in process")
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
    return redirect("/login?message=Could not authenticate user")
  }

  return redirect(data.url)
}

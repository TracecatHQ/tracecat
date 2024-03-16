import React from "react"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"

import Login from "@/components/auth/login"

export default async function HomePage({
  searchParams,
}: {
  searchParams: { message: string }
}) {
  const supabase = createClient()
  const {
    data: { session },
  } = await supabase.auth.getSession()

  if (session) {
    return redirect("/workflows")
  }

  return <Login searchParams={searchParams} />
}

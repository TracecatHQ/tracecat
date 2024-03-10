import React from "react"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"

import { WorkflowsDashboard } from "./workflows-dashboard"

export default async function Page() {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()
  if (!session) {
    redirect("/login")
  }
  return <WorkflowsDashboard session={session} />
}

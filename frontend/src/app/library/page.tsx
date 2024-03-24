import React from "react"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"

import { Library } from "@/components/library/workflow-catalog"
import Navbar from "@/components/nav/navbar"

export default async function Page() {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()
  if (!session) {
    redirect("/")
  }
  return (
    <div className="no-scrollbar flex h-screen max-h-screen flex-col">
      <Navbar session={session} />
      <Library session={session} />
    </div>
  )
}

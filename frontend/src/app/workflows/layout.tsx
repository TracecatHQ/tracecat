import { PropsWithChildren } from "react"
import { Metadata } from "next"
import { redirect } from "next/navigation"
import { DefaultQueryClientProvider } from "@/providers/query"
import { WorkflowProvider } from "@/providers/workflow"
import { createClient } from "@/utils/supabase/server"

import Navbar from "@/components/nav/navbar"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default async function WorkflowsLayout({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()
  return (
    <>
      <DefaultQueryClientProvider>
        <WorkflowProvider session={session}>
          <div className="no-scrollbar flex h-screen flex-col">
            <Navbar session={session} />
            {children}
          </div>
        </WorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}

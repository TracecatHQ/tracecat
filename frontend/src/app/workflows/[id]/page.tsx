import { Metadata } from "next"
import { cookies } from "next/headers"
import { DefaultQueryClientProvider } from "@/providers/query"
import { WorkflowProvider } from "@/providers/workflow"

import { Navbar } from "@/components/navbar"
import { Workspace } from "@/components/workspace"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default function DashboardPage() {
  const layout = cookies().get("react-resizable-panels:layout")
  const defaultLayout = layout ? JSON.parse(layout.value) : undefined
  const collapsed = cookies().get("react-resizable-panels:collapsed")
  let defaultCollapsed

  // Explicitly check for both `undefined` and the string "undefined"
  if (collapsed?.value === undefined || collapsed?.value === "undefined") {
    defaultCollapsed = false
  } else {
    try {
      // Safely attempt to parse `collapsed.value` if it exists
      defaultCollapsed = collapsed ? JSON.parse(collapsed.value) : undefined
    } catch (error) {
      defaultCollapsed = false // Or set to a sensible default
    }
  }

  return (
    <>
      <DefaultQueryClientProvider>
        <WorkflowProvider>
          <div className="flex h-screen flex-col">
            <Navbar />
            <Workspace
              defaultLayout={defaultLayout}
              defaultCollapsed={defaultCollapsed}
              navCollapsedSize={4}
            />
          </div>
        </WorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}

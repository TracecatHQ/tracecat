import { type Metadata } from "next"
import { cookies } from "next/headers"

import { Workspace } from "@/components/workspace/workspace"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default function WorkflowBuilderPage() {
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
    <Workspace
      defaultLayout={defaultLayout}
      defaultCollapsed={defaultCollapsed}
      navCollapsedSize={2}
    />
  )
}

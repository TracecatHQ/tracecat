import { cookies } from "next/headers"

import { DefaultQueryClientProvider } from "@/providers/query"
import { Metadata } from "next"
import { SelectedWorkflowProvider } from "@/providers/selected-workflow"
import { Workspace } from "@/components/workspace"
import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default function DashboardPage() {
  const layout = cookies().get("react-resizable-panels:layout")
  const defaultLayout = layout ? JSON.parse(layout.value) : undefined
  const collapsed = cookies().get("react-resizable-panels:collapsed");
  let defaultCollapsed;

  // Explicitly check for both `undefined` and the string "undefined"
  if (collapsed?.value === undefined || collapsed?.value === "undefined") {
    defaultCollapsed = false;
  } else {
    try {
      // Safely attempt to parse `collapsed.value` if it exists
      defaultCollapsed = collapsed ? JSON.parse(collapsed.value) : undefined;
    } catch (error) {
      defaultCollapsed = false; // Or set to a sensible default
    }
  }

  return (
    <>
      <DefaultQueryClientProvider>
        <SelectedWorkflowProvider>
          <div className="flex flex-col h-screen">
            <Navbar />
            <div className="flex flex-col flex-grow">
              <Workspace
                defaultLayout={defaultLayout}
                defaultCollapsed={defaultCollapsed}
                navCollapsedSize={4}
              />
            </div>
          </div>
        </SelectedWorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}

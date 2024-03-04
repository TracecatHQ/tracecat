import { cookies } from "next/headers"

import { Metadata } from "next"
import { Search } from "@/components/search"
import { Workspace } from "@/components/workspace"
import WorkflowSwitcher from "@/components/workflow-switcher"
import { UserNav } from "@/components/user-nav"
import { DefaultQueryClientProvider } from "@/providers/query"
import { SelectedWorkflowProvider } from "@/providers/selected-workflow"

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
          <div className="border-b">
            <div className="flex h-16 items-center px-4">
              <WorkflowSwitcher />
              <div className="ml-auto flex items-center space-x-4">
                <Search />
                <UserNav />
              </div>
            </div>
          </div>
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

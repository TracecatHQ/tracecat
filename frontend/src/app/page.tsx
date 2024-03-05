import { cookies } from "next/headers"

import { DefaultQueryClientProvider } from "@/providers/query"
import { Label } from "@/components/ui/label"
import { Metadata } from "next"
import { SelectedWorkflowProvider } from "@/providers/selected-workflow"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { UserNav } from "@/components/user-nav"
import { Workspace } from "@/components/workspace"
import WorkflowSwitcher from "@/components/workflow-switcher"

import {
  WorkflowIcon,
  BellRingIcon,
} from "lucide-react"

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
              <div className="flex space-x-8">
                <WorkflowSwitcher />
                <Tabs defaultValue="workspace-view">
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="workflow">
                      <WorkflowIcon className="h-4 w-4 mr-2" />
                      Workflow
                    </TabsTrigger>
                    <TabsTrigger value="cases">
                      <BellRingIcon className="h-4 w-4 mr-2" />
                      Cases
                    </TabsTrigger>
                  </TabsList>
                </Tabs>
              </div>
              <div className="ml-auto flex items-center space-x-8">
                <div className="flex items-center space-x-2">
                  <Switch id="airplane-mode" />
                  <Label htmlFor="airplane-mode">Enable workflow</Label>
                </div>
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

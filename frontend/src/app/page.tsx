import { Metadata } from "next"
import { Search } from "@/components/search"
import WorkflowSwitcher from "@/components/workflow-switcher"
import { UserNav } from "@/components/user-nav"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default function DashboardPage() {
  return (
    <>
      <div className="border-b">
        <div className="flex h-16 items-center px-4">
          <WorkflowSwitcher />
          <div className="ml-auto flex items-center space-x-4">
            <Search />
            <UserNav />
          </div>
        </div>
      </div>
    </>
  )
}

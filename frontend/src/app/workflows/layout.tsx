import { PropsWithChildren } from "react"
import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"
import { WorkflowProvider } from "@/providers/workflow"

import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default function WorkflowsLayout({
  children,
}: PropsWithChildren<React.HTMLAttributes<HTMLDivElement>>) {
  return (
    <>
      <DefaultQueryClientProvider>
        <WorkflowProvider>
          <div className="no-scrollbar flex h-screen flex-col">
            <Navbar />
            {children}
          </div>
        </WorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}

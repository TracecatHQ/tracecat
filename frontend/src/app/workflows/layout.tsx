import { PropsWithChildren } from "react"
import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"

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
        <div className="no-scrollbar flex h-screen flex-col">
          <Navbar />
          {children}
        </div>
      </DefaultQueryClientProvider>
    </>
  )
}

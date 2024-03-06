import { Metadata } from "next"
import { DefaultQueryClientProvider } from "@/providers/query"
import { SelectedWorkflowProvider } from "@/providers/selected-workflow"

import { Navbar } from "@/components/navbar"

export const metadata: Metadata = {
  title: "Cases | Tracecat",
}

export default function CasesPage() {
  return (
    <>
      <DefaultQueryClientProvider>
        <SelectedWorkflowProvider>
          <div className="flex h-screen flex-col">
            <Navbar />
            <h1>Cases</h1>
          </div>
        </SelectedWorkflowProvider>
      </DefaultQueryClientProvider>
    </>
  )
}

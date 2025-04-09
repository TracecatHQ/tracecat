import { Suspense } from "react"
import { Metadata } from "next"

import { CasesSidebar } from "@/components/cases/cases-side-nav"
import { CenteredSpinner } from "@/components/loading/spinner"

export const metadata: Metadata = {
  title: "Cases",
  description: "Cases",
}
export default async function CasesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="no-scrollbar flex h-screen max-h-screen flex-col overflow-hidden">
      <div className="container h-full space-y-6 overflow-auto md:block">
        <div className="flex h-full flex-row space-y-8 lg:space-x-12 lg:space-y-0">
          <aside className="-mx-4 h-full w-1/5">
            <CasesSidebar />
          </aside>
          <div className="no-scrollbar size-full flex-1 overflow-auto">
            <div className="container my-16">
              <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

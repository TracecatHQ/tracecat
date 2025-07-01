import type { Metadata } from "next"
import { Suspense } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"

export const metadata: Metadata = {
  title: "Integrations",
}

export default function IntegrationsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="no-scrollbar h-screen max-h-screen overflow-auto">
      <div className="no-scrollbar container h-full space-y-6 overflow-auto md:block">
        <div className="size-full flex-1 my-16">
          <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
        </div>
      </div>
    </div>
  )
}

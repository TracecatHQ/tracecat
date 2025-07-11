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
    <div className="size-full flex-1 my-16">
      <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
    </div>
  )
}

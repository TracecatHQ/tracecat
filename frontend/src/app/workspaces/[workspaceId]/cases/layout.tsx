import type { Metadata } from "next"
import { CasesRouteLayout } from "@/components/cases/cases-route-layout"

export const metadata: Metadata = {
  title: "Cases",
}

export default function CasesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return <CasesRouteLayout>{children}</CasesRouteLayout>
}

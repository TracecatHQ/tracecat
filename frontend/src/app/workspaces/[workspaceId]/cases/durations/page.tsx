import type { Metadata } from "next"

import { CaseDurationsView } from "@/components/cases/case-durations-view"

export const metadata: Metadata = {
  title: "Durations",
}

export default function CasesDurationsPage() {
  return <CaseDurationsView />
}

import type { Metadata } from "next"
import { ClosureRequirementsView } from "@/components/cases/closure-requirements-view"

export const metadata: Metadata = {
  title: "Closure requirements",
}

export default function ClosureRequirementsPage() {
  return <ClosureRequirementsView />
}

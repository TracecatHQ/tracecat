import type { Metadata } from "next"
import { CasesPageContent } from "@/components/cases/cases-page-content"

export const metadata: Metadata = {
  title: "Cases",
}

export default function CasesPage() {
  return <CasesPageContent />
}

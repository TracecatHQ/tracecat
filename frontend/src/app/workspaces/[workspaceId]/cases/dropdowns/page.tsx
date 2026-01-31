import type { Metadata } from "next"
import { DropdownsView } from "@/components/cases/dropdowns-view"

export const metadata: Metadata = {
  title: "Dropdowns",
}

export default function CasesDropdownsPage() {
  return <DropdownsView />
}

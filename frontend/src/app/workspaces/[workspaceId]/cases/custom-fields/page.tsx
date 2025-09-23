import type { Metadata } from "next"
import { CustomFieldsView } from "@/components/cases/custom-fields-view"

export const metadata: Metadata = {
  title: "Custom fields",
}

export default function CasesCustomFieldsPage() {
  return <CustomFieldsView />
}

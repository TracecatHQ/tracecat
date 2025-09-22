import { CustomFieldsView } from "@/components/cases/custom-fields-view"
import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Custom fields",
}

export default function CasesCustomFieldsPage() {
  return <CustomFieldsView />
}

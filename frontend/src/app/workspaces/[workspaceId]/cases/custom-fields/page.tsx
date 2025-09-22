"use client"

import { useEffect } from "react"
import { CustomFieldsView } from "@/components/cases/custom-fields-view"

export default function CasesCustomFieldsPage() {
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Custom fields"
    }
  }, [])

  return <CustomFieldsView />
}

"use client"

import { useParams } from "next/navigation"
import { FormDetail } from "@/components/forms/form-detail"

export default function FormDetailPage() {
  const params = useParams<{ formId: string }>()
  return <FormDetail formId={params?.formId ?? ""} />
}

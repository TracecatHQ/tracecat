"use client"

import { useParams } from "next/navigation"
import { RequesterForm } from "@/components/forms/requester/requester-form"

export default function FormPage() {
  const { formId } = useParams<{ formId: string }>()
  // Keyed so a different form never inherits the state of the previous one.
  return <RequesterForm key={formId} formId={formId} />
}

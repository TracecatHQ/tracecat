"use client"

import { Suspense } from "react"
import { RequesterHome } from "@/components/forms/requester/requester-home"
import { CenteredSpinner } from "@/components/loading/spinner"

export default function FormsPage() {
  return (
    <Suspense fallback={<CenteredSpinner />}>
      <RequesterHome />
    </Suspense>
  )
}

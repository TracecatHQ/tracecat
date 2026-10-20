"use client"

import type { ReactNode } from "react"
import { RequesterShell } from "@/components/forms/requester-pages"

export default function FormsLayout({ children }: { children: ReactNode }) {
  return <RequesterShell>{children}</RequesterShell>
}

"use client"

import { usePathname } from "next/navigation"
import { SettingsModal } from "@/components/settings/settings-modal"

export function SettingsModalHost() {
  const pathname = usePathname()

  if (pathname?.startsWith("/workspaces")) {
    return null
  }

  return <SettingsModal />
}

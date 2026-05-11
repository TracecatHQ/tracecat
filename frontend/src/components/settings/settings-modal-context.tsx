"use client"

import { createContext, useCallback, useContext, useState } from "react"

export type SettingsSection =
  | "profile"
  | "workspace-general"
  | "workspace-runtime"
  | "workspace-models"
  | "workspace-files"
  | "workspace-sync"

interface SettingsModalContextValue {
  open: boolean
  setOpen: (open: boolean) => void
  activeSection: SettingsSection
  setActiveSection: (section: SettingsSection) => void
}

const SettingsModalContext = createContext<SettingsModalContextValue | null>(
  null
)

export function SettingsModalProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [open, setOpenRaw] = useState(false)
  const [activeSection, setActiveSection] = useState<SettingsSection>("profile")

  const setOpen = useCallback((value: boolean) => {
    setOpenRaw(value)
    if (!value) {
      setActiveSection("profile")
    }
  }, [])

  return (
    <SettingsModalContext.Provider
      value={{ open, setOpen, activeSection, setActiveSection }}
    >
      {children}
    </SettingsModalContext.Provider>
  )
}

export function useSettingsModal() {
  const ctx = useContext(SettingsModalContext)
  if (!ctx) {
    throw new Error(
      "useSettingsModal must be used within SettingsModalProvider"
    )
  }
  return ctx
}

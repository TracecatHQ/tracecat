"use client"

import { createContext, useContext, useState } from "react"

interface SettingsModalContextValue {
  open: boolean
  setOpen: (open: boolean) => void
}

const SettingsModalContext = createContext<SettingsModalContextValue | null>(
  null
)

export function SettingsModalProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  return (
    <SettingsModalContext.Provider value={{ open, setOpen }}>
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

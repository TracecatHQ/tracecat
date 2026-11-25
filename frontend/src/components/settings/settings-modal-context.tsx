"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"

const SETTINGS_SECTIONS = [
  "profile",
  "appearance",
  "security",
  "linked-accounts",
  "workspace-general",
  "workspace-runtime",
  "workspace-cases",
  "workspace-models",
  "workspace-files",
] as const

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]

function isSettingsSection(value: string | null): value is SettingsSection {
  return SETTINGS_SECTIONS.some((section) => section === value)
}

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

  // `?settings=<section>` opens Settings at that section, e.g. after a sign-in redirect.
  useEffect(() => {
    const url = new URL(window.location.href)
    const section = url.searchParams.get("settings")
    if (!isSettingsSection(section)) return
    url.searchParams.delete("settings")
    window.history.replaceState(window.history.state, "", url.toString())
    setActiveSection(section)
    setOpenRaw(true)
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

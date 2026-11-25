"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
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
  /** Open at `section`, handing it `params` once through `takeSectionParams`. */
  openAt: (section: SettingsSection, params?: URLSearchParams) => void
  takeSectionParams: () => URLSearchParams | null
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

  const sectionParams = useRef<URLSearchParams | null>(null)

  const setOpen = useCallback((value: boolean) => {
    setOpenRaw(value)
    if (!value) {
      setActiveSection("profile")
      sectionParams.current = null
    }
  }, [])

  const openAt = useCallback(
    (section: SettingsSection, params?: URLSearchParams) => {
      sectionParams.current = params ?? null
      setActiveSection(section)
      setOpenRaw(true)
    },
    []
  )

  const takeSectionParams = useCallback(() => {
    const params = sectionParams.current
    sectionParams.current = null
    return params
  }, [])

  return (
    <SettingsModalContext.Provider
      value={{
        open,
        setOpen,
        activeSection,
        setActiveSection,
        openAt,
        takeSectionParams,
      }}
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

/**
 * Open Settings from `?settings=<section>` once `signedIn`, handing that section a copy
 * of the query. Captured on mount, before any landing redirect drops it.
 */
export function useSettingsDeepLink(signedIn: boolean) {
  const { openAt } = useSettingsModal()
  const pending = useRef<{
    section: SettingsSection
    params: URLSearchParams
  } | null>(null)

  useEffect(() => {
    const url = new URL(window.location.href)
    const section = url.searchParams.get("settings")
    if (!isSettingsSection(section)) return
    url.searchParams.delete("settings")
    pending.current = { section, params: new URLSearchParams(url.searchParams) }
    window.history.replaceState(window.history.state, "", url.toString())
  }, [])

  useEffect(() => {
    if (!signedIn || !pending.current) return
    const { section, params } = pending.current
    pending.current = null
    openAt(section, params)
  }, [signedIn, openAt])
}

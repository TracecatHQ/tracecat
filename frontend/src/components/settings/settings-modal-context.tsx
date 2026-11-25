"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react"
import { toast } from "@/components/ui/use-toast"
import { linkFailureMessage } from "@/lib/channel-link-failures"

export type SettingsSection =
  | "profile"
  | "appearance"
  | "security"
  | "linked-accounts"
  | "workspace-general"
  | "workspace-runtime"
  | "workspace-cases"
  | "workspace-models"
  | "workspace-files"

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

  // A Slack sign-in started from Settings returns here; reopen Linked accounts.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get("settings") !== "linked-accounts") return
    const result = params.get("result")
    const errorCode = params.get("error") ?? ""
    for (const key of ["settings", "result", "error"]) params.delete(key)
    const query = params.toString()
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
    )
    setActiveSection("linked-accounts")
    setOpenRaw(true)
    if (result === "linked") {
      toast({ title: "Slack account linked" })
    } else if (result === "error") {
      toast({
        title: "Couldn't link your Slack account",
        description: linkFailureMessage(errorCode),
        variant: "destructive",
      })
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

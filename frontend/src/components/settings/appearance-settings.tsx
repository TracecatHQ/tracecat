"use client"

import { Monitor, Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"
import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"

const THEME_OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const

/**
 * Theme selector for the Settings modal Appearance section.
 *
 * Lets the user choose between light, dark, and system themes via
 * `next-themes`. Guards against SSR/hydration mismatches by treating the
 * theme as unset until the component has mounted on the client.
 */
export function AppearanceSettings() {
  const { theme, setTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
  }, [])

  // next-themes returns an undefined theme on the first render, so keep a
  // stable placeholder until mounted to avoid an SSR/client markup mismatch.
  const activeTheme = mounted ? theme : undefined

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h2 className="text-lg font-semibold tracking-tight">Appearance</h2>
        <p className="text-sm text-muted-foreground">
          Customize how Tracecat looks on this device.
        </p>
      </div>
      <div className="space-y-3">
        <span className="text-sm font-medium">Theme</span>
        <div className="grid max-w-md grid-cols-3 gap-3">
          {THEME_OPTIONS.map((option) => {
            const isActive = activeTheme === option.value
            return (
              <button
                key={option.value}
                type="button"
                className={cn(
                  "flex flex-col items-center gap-2 rounded-md border p-4 text-sm font-medium",
                  isActive
                    ? "border-primary ring-1 ring-primary"
                    : "border-border hover:border-foreground/30"
                )}
                onClick={() => setTheme(option.value)}
              >
                <option.icon className="size-5" />
                {option.label}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

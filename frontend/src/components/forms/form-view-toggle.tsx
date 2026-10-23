"use client"

import { ActivityIcon, SlidersHorizontalIcon } from "lucide-react"
import Link from "next/link"
import { useSearchParams } from "next/navigation"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

/** The views of a form's builder page, picked by its `tab` search param. */
export type FormView = "config" | "activity"

/** The view of the form page a `tab` search param names. */
export function formViewFromParam(tab: string | null | undefined): FormView {
  return tab === "activity" ? "activity" : "config"
}

const VIEWS = [
  { view: "config", icon: SlidersHorizontalIcon, label: "Config" },
  { view: "activity", icon: ActivityIcon, label: "Activity" },
] as const

/**
 * Icon tabs for the form page header that switch between Config and
 * Activity. The view lives in the URL, so it survives a reload.
 */
export function FormViewToggle({
  workspaceId,
  formId,
}: {
  workspaceId: string
  formId: string
}) {
  const searchParams = useSearchParams()
  const current = formViewFromParam(searchParams?.get("tab"))
  const base = `/workspaces/${workspaceId}/forms/${formId}`

  return (
    <div className="inline-flex items-center rounded-md border bg-transparent">
      <TooltipProvider>
        {VIEWS.map(({ view, icon: Icon, label }, index) => {
          const isActive = current === view
          return (
            <Tooltip key={view}>
              <TooltipTrigger asChild>
                <Link
                  href={view === "activity" ? `${base}?tab=activity` : base}
                  replace
                  aria-label={label}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "flex size-7 items-center justify-center transition-colors",
                    index === 0 ? "rounded-l-sm" : "rounded-r-sm",
                    isActive
                      ? "bg-background text-accent-foreground"
                      : "bg-accent text-muted-foreground hover:bg-muted/50"
                  )}
                >
                  <Icon className="size-3.5" />
                </Link>
              </TooltipTrigger>
              <TooltipContent>
                <p>{label}</p>
              </TooltipContent>
            </Tooltip>
          )
        })}
      </TooltipProvider>
    </div>
  )
}

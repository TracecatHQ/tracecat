"use client"

import { Plus } from "lucide-react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"
import { Button } from "@/components/ui/button"
import { entityEvents } from "@/lib/entity-events"

export function EntityDetailActions() {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const currentTab = searchParams?.get("tab") || "fields"

  const handleTabChange = (tab: string) => {
    const params = new URLSearchParams(searchParams?.toString() || "")
    params.set("tab", tab)
    router.push(`${pathname}?${params.toString()}`)
  }

  return (
    <div className="flex items-center gap-2">
      <div className="flex h-7 items-center rounded-md bg-muted p-0.5 text-muted-foreground">
        <button
          onClick={() => handleTabChange("fields")}
          className={`inline-flex items-center justify-center whitespace-nowrap rounded-sm px-2.5 py-0.5 text-xs font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 ${
            currentTab === "fields"
              ? "bg-background text-foreground shadow-sm"
              : "hover:text-foreground"
          }`}
        >
          Fields
        </button>
        <button
          onClick={() => handleTabChange("settings")}
          className={`inline-flex items-center justify-center whitespace-nowrap rounded-sm px-2.5 py-0.5 text-xs font-medium ring-offset-background transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 ${
            currentTab === "settings"
              ? "bg-background text-foreground shadow-sm"
              : "hover:text-foreground"
          }`}
        >
          Settings
        </button>
      </div>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => entityEvents.emitAddField()}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add field
      </Button>
    </div>
  )
}

"use client"

import { Plus } from "lucide-react"
import { Button } from "@/components/ui/button"
import { entityEvents } from "@/lib/entity-events"

export function EntityDetailActions() {
  return (
    <Button
      variant="outline"
      size="sm"
      className="h-7 bg-white"
      onClick={() => entityEvents.emitAddField()}
    >
      <Plus className="mr-1 h-3.5 w-3.5" />
      Add field
    </Button>
  )
}

"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { AddCaseDropdownDialog } from "@/components/cases/add-case-dropdown-dialog"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export function AddCaseDropdown() {
  const [dialogOpen, setDialogOpen] = useState(false)
  const { hasEntitlement, isLoading } = useEntitlements()

  if (isLoading) {
    return null
  }

  if (!hasEntitlement("case_dropdowns")) {
    return null
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => setDialogOpen(true)}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Create dropdown
      </Button>
      <AddCaseDropdownDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}

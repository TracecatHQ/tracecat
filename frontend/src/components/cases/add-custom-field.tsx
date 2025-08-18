"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { AddCustomFieldDialog } from "@/components/cases/add-custom-field-dialog"
import { Button } from "@/components/ui/button"

export function AddCustomField() {
  const [dialogOpen, setDialogOpen] = useState(false)

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-white"
        onClick={() => setDialogOpen(true)}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add field
      </Button>
      <AddCustomFieldDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}

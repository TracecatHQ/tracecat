"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { AddCaseTagDialog } from "@/components/cases/add-case-tag-dialog"
import { Button } from "@/components/ui/button"

export function AddCaseTag() {
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
        Create tag
      </Button>
      <AddCaseTagDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}

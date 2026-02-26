"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { AddWorkflowTagDialog } from "@/components/dashboard/add-workflow-tag-dialog"
import { Button } from "@/components/ui/button"

export function AddWorkflowTag() {
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
      <AddWorkflowTagDialog open={dialogOpen} onOpenChange={setDialogOpen} />
    </>
  )
}

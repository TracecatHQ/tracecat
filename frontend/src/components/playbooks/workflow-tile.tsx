"use client"

import React from "react"
import { Layers3, PlusCircle } from "lucide-react"

import { WorkflowMetadata } from "@/types/schemas"
import { addLibraryWorkflow } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { toast } from "@/components/ui/use-toast"
import { Icons } from "@/components/icons"

export function LibraryTile({
  catalogItem,
}: {
  catalogItem: WorkflowMetadata
}) {
  const { title, description, id: workflowId } = catalogItem

  const handleAddWorkflow = async () => {
    const response = await addLibraryWorkflow(workflowId)
    if (response.error) {
      console.error("Error adding workflow:", response.error)
    }
    toast({
      title: "Workflow added!",
      description: `The workflow "${title}" has been added to your workspace.`,
    })
  }

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          className={cn(
            "flex min-h-24 flex-col items-start justify-center text-wrap rounded-lg border px-6 py-4 text-left text-sm shadow-md transition-all hover:cursor-pointer hover:bg-accent",
            "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white"
          )}
        >
          <div className="flex w-full items-center justify-center space-x-4">
            <Avatar>
              <AvatarImage
                className="bg-red-600"
                src={catalogItem.icon_url || ""}
              />
              <AvatarFallback className={cn("bg-cyan-200")}>
                <Layers3 className="h-5 w-5 stroke-slate-400" />
              </AvatarFallback>
            </Avatar>
            <div className="flex w-full flex-col gap-1">
              <div className="flex items-center gap-2">
                <div className="font-semibold capitalize">{title}</div>
              </div>
            </div>
          </div>
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader className="space-y-4">
          <DialogTitle>{title}</DialogTitle>

          <DialogDescription className="flex items-center text-sm text-muted-foreground">
            <span>By</span>
            <Icons.logo className="ml-2 h-3 w-3" />
            <span>Tracecat.</span>
          </DialogDescription>
          <DialogDescription>
            {description.length > 0 ? description : "No description available."}
          </DialogDescription>
        </DialogHeader>

        <DialogFooter>
          <DialogClose asChild>
            <Button
              role="combobox"
              className="ml-auto space-x-2"
              onClick={handleAddWorkflow}
            >
              <PlusCircle className="mr-2 h-4 w-4" />
              Add
            </Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

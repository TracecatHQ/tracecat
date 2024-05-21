"use client"

import { useRouter } from "next/navigation"

import { SettingsIcon, Trash2Icon } from "lucide-react"
import { Workflow } from "@/types/schemas"
import { deleteWorkflow } from "@/lib/flow"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { Separator } from "@/components/ui/separator"

export function WorkflowSettings({ workflow }: { workflow: Workflow }) {
  const router = useRouter()
  const handleDeleteWorkflow = async () => {
    console.log("Delete workflow")
    await deleteWorkflow(workflow.id)
    router.push("/workflows")
    toast({
      title: "Workflow deleted",
      description: `Successfully deleted "${workflow.title}".`,
    })
    router.refresh()
  }
  return (
    <Dialog>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="outline"
            size="icon"
          >
            <SettingsIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-4">
          <DialogTrigger asChild>
            <DropdownMenuItem className="text-red-600 text-sm">
              <Trash2Icon className="size-4 mr-2" />
              <span>Delete</span>
            </DropdownMenuItem>
          </DialogTrigger>
        </DropdownMenuContent>
      </DropdownMenu>

      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete workflow</DialogTitle>
          <DialogClose />
        </DialogHeader>
        <Separator />
        <DialogDescription>
          Are you sure you want to permanently delete this workflow?
        </DialogDescription>
        <DialogFooter>
          <Button variant="outline">Cancel</Button>
          <Button
            onClick={handleDeleteWorkflow}
            variant="destructive"
            className="mr-2"
          >
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

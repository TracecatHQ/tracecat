"use client"

import { useRouter } from "next/navigation"
import { WorkflowResponse, workflowsDeleteWorkflow } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { SettingsIcon, Trash2Icon } from "lucide-react"

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
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"

export function WorkflowSettings({ workflow }: { workflow: WorkflowResponse }) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const handleDeleteWorkflow = async () => {
    console.log("Delete workflow")
    await workflowsDeleteWorkflow({ workspaceId, workflowId: workflow.id })
    router.push(`/workspaces/${workspaceId}`)
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
          <Button variant="outline" size="icon">
            <SettingsIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-4">
          <DialogTrigger asChild>
            <DropdownMenuItem className="text-sm text-red-600">
              <Trash2Icon className="mr-2 size-4" />
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

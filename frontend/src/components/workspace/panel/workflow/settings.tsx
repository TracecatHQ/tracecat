"use client"

import { useRouter } from "next/navigation"
import { HamburgerMenuIcon } from "@radix-ui/react-icons"

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

export function WorkflowSettings({ workflow }: { workflow: Workflow }) {
  const router = useRouter()
  const handleDeleteWorkflow = async () => {
    console.log("Delete workflow")
    await deleteWorkflow(workflow.id)
    router.push("/workflows")
    toast({
      title: "Workflow deleted",
      description: `The workflow "${workflow.title}" has been deleted.`,
    })
    router.refresh()
  }
  return (
    <div>
      <Dialog>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="flex h-8 w-8 p-0 data-[state=open]:bg-muted"
            >
              <HamburgerMenuIcon className="h-4 w-4" />
              <span className="sr-only">Open menu</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-[160px]">
            <DropdownMenuItem disabled>Make a copy</DropdownMenuItem>
            <DropdownMenuItem disabled>Favorite</DropdownMenuItem>
            <DialogTrigger asChild>
              <DropdownMenuItem className="text-red-600">
                Delete
              </DropdownMenuItem>
            </DialogTrigger>
          </DropdownMenuContent>
        </DropdownMenu>

        <DialogContent>
          <DialogHeader className="space-y-4">
            <DialogTitle>
              Are you sure you want to delete this workflow?
            </DialogTitle>
            <DialogDescription className="flex items-center text-sm text-foreground">
              You are about to delete the workflow
              <b className="ml-1">{workflow.title}</b>. Proceed?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <DialogClose asChild>
              <Button
                className="ml-auto space-x-2 border-0 font-bold text-white"
                variant="destructive"
                onClick={handleDeleteWorkflow}
              >
                Delete Workflow
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

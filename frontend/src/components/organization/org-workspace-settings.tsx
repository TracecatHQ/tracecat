"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type WorkspaceRead,
  type WorkspaceUpdate,
  workspacesDeleteWorkspace,
  workspacesUpdateWorkspace,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import { OrgWorkspaceDeleteDialog } from "./org-workspace-delete-dialog"

const workspaceSettingsSchema = z.object({
  name: z.string().min(1, "Workspace name is required"),
})

type WorkspaceSettingsForm = z.infer<typeof workspaceSettingsSchema>

interface OrgWorkspaceSettingsProps {
  workspace: WorkspaceRead
  onWorkspaceDeleted?: () => void
}

export function OrgWorkspaceSettings({
  workspace,
  onWorkspaceDeleted,
}: OrgWorkspaceSettingsProps) {
  const queryClient = useQueryClient()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const form = useForm<WorkspaceSettingsForm>({
    resolver: zodResolver(workspaceSettingsSchema),
    defaultValues: {
      name: workspace.name,
    },
  })

  const { mutateAsync: updateWorkspace, isPending: isUpdating } = useMutation({
    mutationFn: async (params: WorkspaceUpdate) => {
      return await workspacesUpdateWorkspace({
        workspaceId: workspace.id,
        requestBody: params,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspace.id] })
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace updated",
        description: "The workspace name has been updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update workspace", error)
      toast({
        title: "Error updating workspace",
        description: "Failed to update the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: deleteWorkspace, isPending: isDeleting } = useMutation({
    mutationFn: async () => {
      return await workspacesDeleteWorkspace({
        workspaceId: workspace.id,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace deleted",
        description: "The workspace has been deleted successfully.",
      })
      onWorkspaceDeleted?.()
    },
    onError: (error) => {
      console.error("Failed to delete workspace", error)
      toast({
        title: "Error deleting workspace",
        description: "Failed to delete the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  const onSubmit = async (values: WorkspaceSettingsForm) => {
    await updateWorkspace({ name: values.name })
  }

  const handleDeleteWorkspace = async () => {
    await deleteWorkspace()
    setDeleteDialogOpen(false)
  }

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium">Workspace name</h3>
          <p className="text-sm text-muted-foreground">
            Change the name of this workspace.
          </p>
        </div>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="Enter workspace name"
                      className="max-w-md"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <Button type="submit" disabled={isUpdating}>
              {isUpdating ? "Saving..." : "Save changes"}
            </Button>
          </form>
        </Form>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium text-destructive">Danger zone</h3>
          <p className="text-sm text-muted-foreground">
            Permanently delete this workspace and all of its data.
          </p>
        </div>
        <div className="rounded-md border border-destructive p-4">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">Delete this workspace</p>
              <p className="text-sm text-muted-foreground">
                Once deleted, this workspace and all its data cannot be
                recovered.
              </p>
            </div>
            <Button
              variant="destructive"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={isDeleting}
            >
              Delete workspace
            </Button>
          </div>
        </div>
      </div>

      <OrgWorkspaceDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        workspaceName={workspace.name}
        onConfirm={handleDeleteWorkspace}
        isDeleting={isDeleting}
      />
    </div>
  )
}

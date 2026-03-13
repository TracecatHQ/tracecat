"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useRouter } from "next/navigation"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { useWorkspaceSettings } from "@/lib/hooks"
import { OrgWorkspaceDeleteDialog } from "../organization/org-workspace-delete-dialog"

export const generalSettingsSchema = z.object({
  name: z.string().trim().min(1, "Workspace name is required"),
})

type GeneralSettingsForm = z.infer<typeof generalSettingsSchema>

interface WorkspaceGeneralSettingsProps {
  workspace: WorkspaceRead
}

export function WorkspaceGeneralSettings({
  workspace,
}: WorkspaceGeneralSettingsProps) {
  const router = useRouter()
  const { setOpen } = useSettingsModal()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)

  const { updateWorkspace, isUpdating, deleteWorkspace, isDeleting } =
    useWorkspaceSettings(workspace.id, () => {
      setOpen(false)
      router.push("/workspaces")
    })

  const form = useForm<GeneralSettingsForm>({
    resolver: zodResolver(generalSettingsSchema),
    mode: "onChange",
    defaultValues: {
      name: workspace.name,
    },
  })

  async function onSubmit(values: GeneralSettingsForm) {
    await updateWorkspace({ name: values.name })
  }

  async function handleDeleteWorkspace() {
    await deleteWorkspace()
    setDeleteDialogOpen(false)
  }

  return (
    <div className="space-y-8">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6">
          <FormField
            control={form.control}
            name="name"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Workspace name</FormLabel>
                <FormControl>
                  <Input
                    {...field}
                    placeholder="Enter workspace name"
                    className="max-w-md"
                  />
                </FormControl>
                <FormDescription>
                  Change the name of this workspace.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button type="submit" disabled={isUpdating} size="sm">
            {isUpdating ? "Saving..." : "Save"}
          </Button>
        </form>
      </Form>

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

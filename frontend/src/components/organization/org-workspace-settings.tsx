"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyRoundIcon, TrashIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretReadMinimal, WorkspaceRead } from "@/client"
import {
  CreateSSHKeyDialog,
  CreateSSHKeyDialogTrigger,
} from "@/components/ssh-keys/ssh-key-create-dialog"
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
import { Switch } from "@/components/ui/switch"
import { useFeatureFlag } from "@/hooks/use-feature-flags"
import { useWorkspaceSettings } from "@/lib/hooks"
import { OrgWorkspaceDeleteDialog } from "./org-workspace-delete-dialog"
import { OrgWorkspaceSSHKeyDeleteDialog } from "./org-workspace-ssh-key-delete-dialog"

const workspaceSettingsSchema = z.object({
  name: z.string().min(1, "Workspace name is required"),
  git_repo_url: z
    .string()
    .nullish()
    .refine(
      (url) => !url || /^git\+ssh:\/\/git@[^/]+\/[^/]+\/[^/@]+\.git$/.test(url),
      "Must be a valid Git SSH URL in format: git+ssh://git@host/org/repo.git"
    ),
  workflow_unlimited_timeout_enabled: z.boolean().optional(),
  workflow_default_timeout_seconds: z
    .number()
    .min(1, "Timeout must be at least 1 second")
    .optional(),
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
  const { isFeatureEnabled } = useFeatureFlag()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [sshKeyToDelete, setSSHKeyToDelete] =
    useState<SecretReadMinimal | null>(null)
  const {
    sshKeys,
    sshKeysLoading,
    updateWorkspace,
    isUpdating,
    deleteWorkspace,
    isDeleting,
    handleCreateWorkspaceSSHKey,
    handleDeleteSSHKey,
  } = useWorkspaceSettings(workspace.id, onWorkspaceDeleted)

  const form = useForm<WorkspaceSettingsForm>({
    resolver: zodResolver(workspaceSettingsSchema),
    defaultValues: {
      name: workspace.name,
      git_repo_url: workspace.settings?.git_repo_url || "",
      workflow_unlimited_timeout_enabled:
        workspace.settings?.workflow_unlimited_timeout_enabled ?? false,
      workflow_default_timeout_seconds:
        workspace.settings?.workflow_default_timeout_seconds || undefined,
    },
  })

  const onSubmit = async (values: WorkspaceSettingsForm) => {
    await updateWorkspace({
      name: values.name,
      settings: {
        git_repo_url: values.git_repo_url,
        workflow_unlimited_timeout_enabled:
          values.workflow_unlimited_timeout_enabled,
        workflow_default_timeout_seconds:
          values.workflow_default_timeout_seconds,
      },
    })
  }

  const handleDeleteWorkspace = async () => {
    await deleteWorkspace()
    setDeleteDialogOpen(false)
  }

  const handleDeleteSSHKeyWrapper = async () => {
    if (!sshKeyToDelete) return
    try {
      await handleDeleteSSHKey(sshKeyToDelete)
      setSSHKeyToDelete(null)
    } catch (error) {
      console.error("Failed to delete SSH key:", error)
    }
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
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
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

            {isFeatureEnabled("git-sync") && (
              <div>
                <h4 className="text-md font-medium mb-4">
                  Git repository settings
                </h4>
                <p className="text-sm text-muted-foreground mb-4">
                  Configure the Git repository used to store and sync workflow
                  definitions for this workspace.
                </p>
                <div className="space-y-4">
                  <FormField
                    control={form.control}
                    name="git_repo_url"
                    render={({ field }) => (
                      <FormItem className="flex flex-col">
                        <FormLabel>Remote repository URL</FormLabel>
                        <FormControl>
                          <Input
                            placeholder="git+ssh://git@my-host/my-org/my-repo.git"
                            {...field}
                            value={field.value ?? ""}
                          />
                        </FormControl>
                        <FormDescription>
                          Git URL of the remote repository. Must use{" "}
                          <span className="font-mono tracking-tighter">
                            git+ssh
                          </span>{" "}
                          scheme.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
              </div>
            )}

            <div>
              <h4 className="text-md font-medium mb-4">
                Workflow timeout settings
              </h4>
              <p className="text-sm text-muted-foreground mb-4">
                Configure default timeout behavior for all workflows in this
                workspace.
              </p>
              <div className="space-y-4">
                <FormField
                  control={form.control}
                  name="workflow_unlimited_timeout_enabled"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                      <div className="space-y-0.5">
                        <FormLabel className="text-base">
                          Unlimited workflow timeout
                        </FormLabel>
                        <FormDescription>
                          Allow workflows to run indefinitely without timeout
                          constraints. When enabled, individual workflow timeout
                          settings are ignored.
                        </FormDescription>
                      </div>
                      <FormControl>
                        <Switch
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="workflow_default_timeout_seconds"
                  render={({ field }) => (
                    <FormItem className="flex flex-col">
                      <FormLabel>Default workflow timeout (seconds)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          placeholder="300"
                          {...field}
                          value={field.value ?? ""}
                          onChange={(e) =>
                            field.onChange(
                              e.target.value
                                ? Number(e.target.value)
                                : undefined
                            )
                          }
                          disabled={form.watch(
                            "workflow_unlimited_timeout_enabled"
                          )}
                          className="max-w-md"
                        />
                      </FormControl>
                      <FormDescription>
                        Default timeout in seconds for workflows in this
                        workspace. Individual workflow settings will fall back
                        to this value. Leave empty to use per-workflow timeout
                        settings.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>

            <Button type="submit" disabled={isUpdating}>
              {isUpdating ? "Saving..." : "Save changes"}
            </Button>
          </form>
        </Form>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium">SSH key management</h3>
          <p className="text-sm text-muted-foreground">
            Manage SSH keys for authenticating with private Git repositories.
          </p>
        </div>

        {/* Display existing SSH keys */}
        {sshKeysLoading ? (
          <div className="text-sm text-muted-foreground">
            Loading SSH keys...
          </div>
        ) : sshKeys && sshKeys.length > 0 ? (
          <div className="space-y-2">
            {sshKeys.map((sshKey) => (
              <div
                key={sshKey.id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div className="flex items-center space-x-3">
                  <KeyRoundIcon className="size-4 text-muted-foreground" />
                  <div>
                    <div className="font-medium">{sshKey.name}</div>
                    {sshKey.description && (
                      <div className="text-sm text-muted-foreground">
                        {sshKey.description}
                      </div>
                    )}
                    {sshKey.environment !== "default" && (
                      <div className="text-xs text-muted-foreground">
                        Environment: {sshKey.environment}
                      </div>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSSHKeyToDelete(sshKey)}
                  className="text-destructive hover:text-destructive"
                >
                  <TrashIcon className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">
            No SSH keys configured. Create one to authenticate with private Git
            repositories.
          </div>
        )}

        <CreateSSHKeyDialog
          handler={handleCreateWorkspaceSSHKey}
          fieldConfig={{
            name: {
              defaultValue: "store-ssh-key",
              disabled: true,
            },
          }}
        >
          <CreateSSHKeyDialogTrigger asChild>
            <Button variant="outline" className="space-x-2">
              <KeyRoundIcon className="mr-2 size-4" />
              Create SSH key
            </Button>
          </CreateSSHKeyDialogTrigger>
        </CreateSSHKeyDialog>
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

      {/* SSH Key Delete Confirmation Dialog */}
      <OrgWorkspaceSSHKeyDeleteDialog
        open={!!sshKeyToDelete}
        onOpenChange={(open) => !open && setSSHKeyToDelete(null)}
        sshKey={sshKeyToDelete}
        onConfirm={handleDeleteSSHKeyWrapper}
      />
    </div>
  )
}

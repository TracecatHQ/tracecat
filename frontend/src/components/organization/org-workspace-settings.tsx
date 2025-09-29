"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  GitPullRequestIcon,
  KeyRoundIcon,
  RefreshCwIcon,
  TrashIcon,
} from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { SecretCreate, SecretReadMinimal, WorkspaceRead } from "@/client"
import {
  CreateSSHKeyDialog,
  CreateSSHKeyDialogTrigger,
} from "@/components/ssh-keys/ssh-key-create-dialog"
import { CustomTagInput } from "@/components/tags-input"
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
import { validateGitSshUrl } from "@/lib/git"
import { useOrgSecrets, useWorkspaceSettings } from "@/lib/hooks"
import { OrgWorkspaceDeleteDialog } from "./org-workspace-delete-dialog"
import { OrgWorkspaceSSHKeyDeleteDialog } from "./org-workspace-ssh-key-delete-dialog"
import { WorkflowPullDialog } from "./workflow-pull-dialog"

export const workspaceSettingsSchema = z.object({
  name: z.string().min(1, "Workspace name is required"),
  git_repo_url: z
    .string()
    .nullish()
    .transform((url) => url?.trim() || null)
    .superRefine((url, ctx) => validateGitSshUrl(url, ctx)),
  workflow_unlimited_timeout_enabled: z.boolean().optional(),
  workflow_default_timeout_seconds: z
    .number()
    .min(1, "Timeout must be at least 1 second")
    .optional(),
  allowed_attachment_extensions: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().min(1, "Cannot be empty"),
      })
    )
    .optional(),
  allowed_attachment_mime_types: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().min(1, "Cannot be empty"),
      })
    )
    .optional(),
  validate_attachment_magic_number: z.boolean().optional(),
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
  // Get the system defaults from the workspace response
  const systemDefaultExtensions =
    workspace.settings?.effective_allowed_attachment_extensions || []
  const systemDefaultMimeTypes =
    workspace.settings?.effective_allowed_attachment_mime_types || []
  const { isFeatureEnabled } = useFeatureFlag()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [pullDialogOpen, setPullDialogOpen] = useState(false)
  const [sshKeyToDelete, setSSHKeyToDelete] =
    useState<SecretReadMinimal | null>(null)
  const { updateWorkspace, isUpdating, deleteWorkspace, isDeleting } =
    useWorkspaceSettings(workspace.id, onWorkspaceDeleted)
  const {
    orgSSHKeys: sshKeys,
    orgSSHKeysIsLoading: sshKeysLoading,
    createSecret,
    deleteSecretById,
  } = useOrgSecrets()

  const form = useForm<WorkspaceSettingsForm>({
    resolver: zodResolver(workspaceSettingsSchema),
    mode: "onChange",
    reValidateMode: "onChange",
    defaultValues: {
      name: workspace.name,
      git_repo_url: workspace.settings?.git_repo_url || "",
      workflow_unlimited_timeout_enabled:
        workspace.settings?.workflow_unlimited_timeout_enabled ?? false,
      workflow_default_timeout_seconds:
        workspace.settings?.workflow_default_timeout_seconds || undefined,
      // If no explicit overrides exist, leave unset to preserve inheritance
      allowed_attachment_extensions: workspace.settings
        ?.allowed_attachment_extensions?.length
        ? workspace.settings.allowed_attachment_extensions.map(
            (ext, index) => ({
              id: `ext-${index}`,
              text: ext,
            })
          )
        : undefined,
      // If no explicit overrides exist, leave unset to preserve inheritance
      allowed_attachment_mime_types: workspace.settings
        ?.allowed_attachment_mime_types?.length
        ? workspace.settings.allowed_attachment_mime_types.map(
            (mime, index) => ({
              id: `mime-${index}`,
              text: mime,
            })
          )
        : undefined,
      validate_attachment_magic_number:
        workspace.settings?.validate_attachment_magic_number ?? true,
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
        allowed_attachment_extensions: values.allowed_attachment_extensions
          ?.length
          ? values.allowed_attachment_extensions.map((ext) => ext.text)
          : undefined,
        allowed_attachment_mime_types: values.allowed_attachment_mime_types
          ?.length
          ? values.allowed_attachment_mime_types.map((mime) => mime.text)
          : undefined,
        validate_attachment_magic_number:
          values.validate_attachment_magic_number,
      },
    })
  }

  const handleDeleteWorkspace = async () => {
    await deleteWorkspace()
    setDeleteDialogOpen(false)
  }

  const handleCreateWorkspaceSSHKey = async (data: SecretCreate) => {
    await createSecret({
      ...data,
      type: "ssh-key",
      name: "store-ssh-key",
    })
  }

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
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

                  {/* Workflow Sync Section */}
                  {form.watch("git_repo_url") && (
                    <div className="mt-6 p-4 border rounded-lg bg-muted/30">
                      <div className="flex items-center justify-between mb-4">
                        <div>
                          <h5 className="text-sm font-medium">
                            Workflow synchronization
                          </h5>
                          <p className="text-xs text-muted-foreground">
                            Pull workflow definitions from your Git repository
                            into this workspace
                          </p>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => setPullDialogOpen(true)}
                          className="flex items-center space-x-2"
                        >
                          <GitPullRequestIcon className="size-4" />
                          <span>Pull workflows</span>
                        </Button>
                      </div>

                      <div className="text-xs text-muted-foreground">
                        <p>
                          • Select a commit SHA to pull specific workflow
                          versions
                        </p>
                        <p>
                          • All changes are atomic - either all workflows import
                          or none do
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            <FormField
              control={form.control}
              name="workflow_unlimited_timeout_enabled"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                  <div className="space-y-0.5">
                    <FormLabel>Unlimited workflow timeout</FormLabel>
                    <FormDescription>
                      Force all workflows to run indefinitely without timeout
                      constraints.
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
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                  <div className="space-y-0.5">
                    <FormLabel>Default workflow timeout</FormLabel>
                    <FormDescription>
                      Default timeout in seconds for workflows in this
                      workspace. Disabled if unlimited timeout is enabled.
                    </FormDescription>
                  </div>
                  <FormControl>
                    <Input
                      type="number"
                      min={1}
                      placeholder="300"
                      {...field}
                      value={field.value ?? ""}
                      onChange={(e) =>
                        field.onChange(
                          e.target.value ? Number(e.target.value) : undefined
                        )
                      }
                      disabled={form.watch(
                        "workflow_unlimited_timeout_enabled"
                      )}
                      className="w-24"
                    />
                  </FormControl>
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="allowed_attachment_extensions"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center justify-between">
                    <FormLabel>Allowed file extensions</FormLabel>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        field.onChange(
                          systemDefaultExtensions.map((ext, index) => ({
                            id: `ext-default-${index}`,
                            text: ext,
                          }))
                        )
                      }}
                      className="h-auto p-1"
                    >
                      <RefreshCwIcon className="h-3 w-3" />
                    </Button>
                  </div>
                  <FormControl>
                    <CustomTagInput
                      {...field}
                      placeholder="Enter an extension..."
                      tags={field.value || []}
                      setTags={field.onChange}
                    />
                  </FormControl>
                  <FormDescription>
                    Add file extensions that users can upload as attachments
                    (e.g., .pdf, .docx, .png)
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="allowed_attachment_mime_types"
              render={({ field }) => (
                <FormItem>
                  <div className="flex items-center justify-between">
                    <FormLabel>Allowed MIME types</FormLabel>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        field.onChange(
                          systemDefaultMimeTypes.map((mime, index) => ({
                            id: `mime-default-${index}`,
                            text: mime,
                          }))
                        )
                      }}
                      className="h-auto p-1"
                    >
                      <RefreshCwIcon className="h-3 w-3" />
                    </Button>
                  </div>
                  <FormControl>
                    <CustomTagInput
                      {...field}
                      placeholder="Enter a MIME type..."
                      tags={field.value || []}
                      setTags={field.onChange}
                    />
                  </FormControl>
                  <FormDescription>
                    Add MIME types that are allowed for attachments (e.g.,
                    application/pdf, image/jpeg)
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="validate_attachment_magic_number"
              render={({ field }) => (
                <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
                  <div className="space-y-0.5">
                    <FormLabel>Validate file content</FormLabel>
                    <FormDescription>
                      Verify that uploaded files match their declared type by
                      checking file signatures (magic numbers). Disabling this
                      may allow malicious files disguised as other formats.
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

            <Button type="submit" disabled={isUpdating}>
              {isUpdating ? "Saving..." : "Update workspace settings"}
            </Button>
          </form>
        </Form>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium">Workflow Git sync</h3>
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
                    <div className="text-sm">{sshKey.name}</div>
                    {sshKey.description && (
                      <div className="text-xs text-muted-foreground">
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
        ) : null}

        <div className="space-y-2">
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
          {!sshKeysLoading && (!sshKeys || sshKeys.length === 0) && (
            <div className="text-xs text-muted-foreground">
              No SSH keys configured. Create one to authenticate with private
              Git repositories.
            </div>
          )}
        </div>
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

      {/* SSH Key Delete Dialog */}
      <OrgWorkspaceSSHKeyDeleteDialog
        open={!!sshKeyToDelete}
        onOpenChange={(open) => !open && setSSHKeyToDelete(null)}
        sshKey={sshKeyToDelete}
        onConfirm={async () => {
          if (sshKeyToDelete) {
            await deleteSecretById(sshKeyToDelete)
            setSSHKeyToDelete(null)
          }
        }}
      />

      {/* Workflow Pull Dialog */}
      <WorkflowPullDialog
        open={pullDialogOpen}
        onOpenChange={setPullDialogOpen}
        workspaceId={workspace.id}
        gitRepoUrl={form.watch("git_repo_url") || undefined}
        onPullSuccess={() => {
          // Refresh workspace data or show success message
          console.log("Workflows pulled successfully")
        }}
      />
    </div>
  )
}

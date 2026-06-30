"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  GitBranchIcon,
  Trash2Icon,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import { useToast } from "@/components/ui/use-toast"
import {
  useDeleteGitLabTokenCredentials,
  useGitLabTokenCredentials,
  useGitLabTokenCredentialsStatus,
} from "@/lib/hooks"

const gitLabTokenFormSchema = z.object({
  base_url: z
    .string()
    .trim()
    .url("Please enter a valid URL")
    .default("https://gitlab.com"),
  token: z.string().min(1, "Token is required"),
})

type GitLabTokenFormData = z.infer<typeof gitLabTokenFormSchema>

export function GitLabTokenSetup() {
  const {
    credentialsStatus,
    credentialsStatusIsLoading,
    refetchCredentialsStatus,
  } = useGitLabTokenCredentialsStatus()
  const { deleteCredentials } = useDeleteGitLabTokenCredentials()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const { toast } = useToast()

  const isConfigured = credentialsStatus?.exists ?? false
  const isCorrupted = credentialsStatus?.is_corrupted ?? false

  async function handleDelete() {
    try {
      await deleteCredentials.mutateAsync()
      setDeleteDialogOpen(false)
      toast({
        title: "GitLab credentials deleted",
        description: "GitLab workspace sync has been disconnected.",
      })
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to delete credentials",
        variant: "destructive",
      })
    }
  }

  if (credentialsStatusIsLoading) {
    return <CenteredSpinner />
  }

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="flex items-center gap-3">
          {isCorrupted ? (
            <AlertTriangleIcon className="size-5 text-amber-500" />
          ) : isConfigured ? (
            <CheckCircle2Icon className="size-5 text-green-500" />
          ) : (
            <GitBranchIcon className="size-5 text-muted-foreground" />
          )}
          <div>
            <p className="text-sm font-medium">GitLab</p>
            <p className="text-xs text-muted-foreground">
              {isCorrupted
                ? "Stored credentials are unreadable. Re-enter the GitLab token to reconnect."
                : isConfigured
                  ? `Base URL: ${credentialsStatus?.base_url ?? "unknown"}`
                  : "Not connected"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isConfigured ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDialogOpen(true)}
              >
                {isCorrupted ? "Reconnect" : "Update"}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteDialogOpen(true)}
              >
                <Trash2Icon className="size-3.5" />
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              Connect
            </Button>
          )}
        </div>
      </div>

      <GitLabConnectionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        existingBaseUrl={credentialsStatus?.base_url ?? undefined}
        onFormSuccess={() => {
          setDialogOpen(false)
          refetchCredentialsStatus()
          toast({
            title: "GitLab credentials saved",
            description: "GitLab workspace sync credentials are ready.",
          })
        }}
      />

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete GitLab credentials</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the GitLab token credentials?
              GitLab workspace sync will stop working until credentials are
              reconnected.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteCredentials.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleteCredentials.isPending}
              className="bg-destructive hover:bg-destructive/90"
            >
              {deleteCredentials.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function GitLabConnectionDialog({
  open,
  onOpenChange,
  existingBaseUrl,
  onFormSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingBaseUrl?: string
  onFormSuccess: () => void
}) {
  const { saveCredentials } = useGitLabTokenCredentials()
  const form = useForm<GitLabTokenFormData>({
    resolver: zodResolver(gitLabTokenFormSchema),
    defaultValues: {
      base_url: existingBaseUrl ?? "https://gitlab.com",
      token: "",
    },
  })

  useEffect(() => {
    if (!open) {
      return
    }
    form.reset({
      base_url: existingBaseUrl ?? "https://gitlab.com",
      token: "",
    })
  }, [existingBaseUrl, form, open])

  async function onSubmit(values: GitLabTokenFormData) {
    await saveCredentials.mutateAsync(values)
    onFormSuccess()
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>GitLab workspace sync credential</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="base_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Base URL</FormLabel>
                  <FormControl>
                    <Input placeholder="https://gitlab.com" {...field} />
                  </FormControl>
                  <FormDescription>
                    Use your self-managed GitLab URL when not using GitLab.com.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="token"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Credential token</FormLabel>
                  <FormControl>
                    <Input
                      type="password"
                      autoComplete="off"
                      placeholder="glpat-..."
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Use a GitLab project or group access token with the api
                    scope. Tracecat stores this token as the GitLab credential
                    for workspace sync; prefer it over a personal access token
                    for long-lived sync.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={saveCredentials.isPending}>
                {saveCredentials.isPending ? "Saving..." : "Save"}
              </Button>
            </div>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

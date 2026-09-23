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
  useBitbucketTokenCredentials,
  useBitbucketTokenCredentialsStatus,
  useDeleteBitbucketTokenCredentials,
} from "@/hooks/use-bitbucket-credentials"

const bitbucketTokenFormSchema = z.object({
  email: z.string().trim().email("Enter your Atlassian account email"),
  token: z.string().trim().min(1, "Token is required"),
})

type BitbucketTokenFormData = z.infer<typeof bitbucketTokenFormSchema>

/** Configure organization credentials for Bitbucket Cloud workspace sync. */
export function BitbucketTokenSetup() {
  const {
    credentialsStatus,
    credentialsStatusIsLoading,
    credentialsStatusError,
    refetchCredentialsStatus,
  } = useBitbucketTokenCredentialsStatus()
  const { deleteCredentials } = useDeleteBitbucketTokenCredentials()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const { toast } = useToast()

  const isConfigured = credentialsStatus?.exists ?? false
  const isCorrupted = credentialsStatus?.is_corrupted ?? false

  let statusLabel = "Not connected"
  let StatusIcon = GitBranchIcon
  if (isCorrupted) {
    statusLabel =
      "Stored credentials are unreadable. Re-enter the API token to reconnect."
    StatusIcon = AlertTriangleIcon
  } else if (isConfigured) {
    statusLabel = `Account email: ${credentialsStatus?.email ?? "unknown"}`
    StatusIcon = CheckCircle2Icon
  }

  async function handleDelete() {
    try {
      await deleteCredentials.mutateAsync()
      setDeleteDialogOpen(false)
      toast({
        title: "Bitbucket credentials deleted",
        description: "Bitbucket workspace sync has been disconnected.",
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

  if (credentialsStatusError) {
    return (
      <p className="text-sm text-destructive">
        Unable to load Bitbucket credentials.{" "}
        <Button variant="link" onClick={() => refetchCredentialsStatus()}>
          Retry
        </Button>
      </p>
    )
  }

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="flex items-center gap-3">
          <StatusIcon className="size-5 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">Bitbucket Cloud</p>
            <p className="text-xs text-muted-foreground">{statusLabel}</p>
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
                aria-label="Delete Bitbucket credentials"
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

      <BitbucketConnectionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        existingEmail={credentialsStatus?.email ?? undefined}
        onFormSuccess={() => {
          setDialogOpen(false)
          refetchCredentialsStatus()
          toast({
            title: "Bitbucket credentials saved",
            description: "Bitbucket workspace sync credentials are ready.",
          })
        }}
      />

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Bitbucket credentials</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the Bitbucket token credentials?
              Bitbucket workspace sync will stop working until credentials are
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

function BitbucketConnectionDialog({
  open,
  onOpenChange,
  existingEmail,
  onFormSuccess,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  existingEmail?: string
  onFormSuccess: () => void
}) {
  const { saveCredentials } = useBitbucketTokenCredentials()
  const { toast } = useToast()
  const form = useForm<BitbucketTokenFormData>({
    resolver: zodResolver(bitbucketTokenFormSchema),
    defaultValues: {
      email: existingEmail ?? "",
      token: "",
    },
  })

  useEffect(() => {
    form.reset({
      email: existingEmail ?? "",
      token: "",
    })
  }, [existingEmail, form, open])

  async function onSubmit(values: BitbucketTokenFormData) {
    try {
      await saveCredentials.mutateAsync(values)
      onFormSuccess()
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to save Bitbucket credentials",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Bitbucket workspace sync credential</DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Account email</FormLabel>
                  <FormControl>
                    <Input placeholder="you@example.com" {...field} />
                  </FormControl>
                  <FormDescription>
                    Use the Atlassian account email associated with your API
                    token.
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
                      placeholder="API token"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Create a scoped Bitbucket Cloud API token with repository
                    read/write and pull request read/write permissions. Data
                    Center is not supported.{" "}
                    <a
                      className="underline"
                      href="https://support.atlassian.com/bitbucket-cloud/docs/api-token-permissions/"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Token permissions
                    </a>
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

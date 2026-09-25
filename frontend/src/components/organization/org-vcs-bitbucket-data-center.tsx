"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Trash2Icon } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { BitbucketIcon } from "@/components/organization/vcs-icons"
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
  useBitbucketDataCenterTokenCredentials,
  useBitbucketDataCenterTokenCredentialsStatus,
  useDeleteBitbucketDataCenterTokenCredentials,
} from "@/hooks/use-bitbucket-data-center-credentials"

const bitbucketDataCenterTokenFormSchema = z.object({
  base_url: z
    .string()
    .trim()
    .url("Enter your HTTPS instance URL")
    .startsWith("https://"),
  token: z.string().trim().min(1, "Token is required"),
})

type BitbucketDataCenterTokenFormData = z.infer<
  typeof bitbucketDataCenterTokenFormSchema
>

/** Configure organization credentials for Bitbucket Data Center workspace sync. */
export function BitbucketDataCenterTokenSetup() {
  const {
    credentialsStatus,
    credentialsStatusIsLoading,
    credentialsStatusError,
    refetchCredentialsStatus,
  } = useBitbucketDataCenterTokenCredentialsStatus()
  const { deleteCredentials } = useDeleteBitbucketDataCenterTokenCredentials()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const { toast } = useToast()

  const isConfigured = credentialsStatus?.exists ?? false
  const isCorrupted = credentialsStatus?.is_corrupted ?? false

  let statusLabel = "Not connected"
  if (isCorrupted) {
    statusLabel =
      "Stored credentials are unreadable. Re-enter the API token to reconnect."
  } else if (isConfigured) {
    statusLabel = `Instance URL: ${credentialsStatus?.base_url ?? "unknown"}`
  }

  async function handleDelete() {
    try {
      await deleteCredentials.mutateAsync()
      setDeleteDialogOpen(false)
      toast({
        title: "Bitbucket Data Center credentials deleted",
        description:
          "Bitbucket Data Center workspace sync has been disconnected.",
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
        Unable to load Bitbucket Data Center credentials.{" "}
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
          <BitbucketIcon className="size-5 text-muted-foreground" />
          <div>
            <p className="text-sm font-medium">Bitbucket Data Center</p>
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
                aria-label="Delete Bitbucket Data Center credentials"
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

      <BitbucketDataCenterConnectionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        existingBaseUrl={credentialsStatus?.base_url ?? undefined}
        onFormSuccess={() => {
          setDialogOpen(false)
          refetchCredentialsStatus()
          toast({
            title: "Bitbucket Data Center credentials saved",
            description:
              "Bitbucket Data Center workspace sync credentials are ready.",
          })
        }}
      />

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete Bitbucket Data Center credentials
            </AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the Bitbucket Data Center token
              credentials? Bitbucket Data Center workspace sync will stop
              working until credentials are reconnected.
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

function BitbucketDataCenterConnectionDialog({
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
  const { saveCredentials } = useBitbucketDataCenterTokenCredentials()
  const { toast } = useToast()
  const form = useForm<BitbucketDataCenterTokenFormData>({
    resolver: zodResolver(bitbucketDataCenterTokenFormSchema),
    defaultValues: {
      base_url: existingBaseUrl ?? "",
      token: "",
    },
  })

  useEffect(() => {
    form.reset({
      base_url: existingBaseUrl ?? "",
      token: "",
    })
  }, [existingBaseUrl, form, open])

  async function onSubmit(values: BitbucketDataCenterTokenFormData) {
    try {
      await saveCredentials.mutateAsync(values)
      onFormSuccess()
    } catch (error) {
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to save Bitbucket Data Center credentials",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            Bitbucket Data Center workspace sync credential
          </DialogTitle>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="base_url"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Instance URL</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="https://bitbucket.example.com"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Use the HTTPS instance URL, including its context path if
                    configured.
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
                      placeholder="HTTP access token"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    Use a Data Center HTTP access token with repository write
                    permission.{" "}
                    <a
                      className="underline"
                      href="https://confluence.atlassian.com/bitbucketserver/http-access-tokens-939515499.html"
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

"use client"

import { useSession } from "@/providers/session"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PlusCircle } from "lucide-react"

import { Secret } from "@/types/schemas"
import { deleteSecret, fetchAllSecrets } from "@/lib/secrets"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/new-credential-dialog"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

export default function CredentialsPage() {
  const session = useSession()
  const queryClient = useQueryClient()
  const {
    data: secrets,
    isLoading,
    error,
  } = useQuery<Secret[], Error>({
    queryKey: ["secrets"],
    queryFn: async () => await fetchAllSecrets(session),
  })
  const { mutate } = useMutation({
    mutationFn: async (secret: Secret) => {
      // Fix for Problem 1: Update mutationFn to return a Promise
      if (!secret.id) {
        // Fix for Problem 2: Provide a default value for secret?.id
        console.error("No secret provided to delete")
        return
      }
      await deleteSecret(session, secret?.id) // Fix for Problem 2: Await the deleteSecret function
    },
    onSuccess: (data, variables, context) => {
      queryClient.invalidateQueries({ queryKey: ["secrets"] })
      toast({
        title: "Deleted secret",
        description: "Secret deleted successfully.",
      })
    },
    onError: (error, variables, context) => {
      console.error("Failed to delete credentials", error)
      toast({
        title: "Failed to delete secret",
        description: "An error occurred while deleting the secret.",
      })
    },
  })

  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    return <AlertNotification level="error" message={error.message} />
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-lg font-medium">Credentials</h3>
      </div>
      <Separator />
      <NewCredentialsDialog>
        <NewCredentialsDialogTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            className="ml-auto space-x-2"
          >
            <PlusCircle className="mr-2 h-4 w-4" />
            New
          </Button>
        </NewCredentialsDialogTrigger>
      </NewCredentialsDialog>
      <div className="space-y-4">
        {secrets ? (
          secrets?.map((secret, idx) => (
            <div
              key={idx}
              className="flex items-center justify-center space-x-4"
            >
              <Input className="text-sm" value={secret.name} readOnly />
              <Input
                className="text-sm"
                value={`${secret.value.substring(0, 3)}...`}
                readOnly
              />
              <Button variant="destructive" onClick={() => mutate(secret)}>
                Delete
              </Button>
            </div>
          ))
        ) : (
          <NoContent message="No credentials found" />
        )}
      </div>
    </div>
  )
}

"use client"

import { useSession } from "@/providers/session"
import { Label } from "@radix-ui/react-label"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { PlusCircle, Trash2Icon } from "lucide-react"

import { Secret } from "@/types/schemas"
import { deleteSecret, fetchAllSecrets } from "@/lib/secrets"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
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
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-medium">Credentials</h3>
        <NewCredentialsDialog>
          <NewCredentialsDialogTrigger asChild>
            <Button
              variant="outline"
              role="combobox"
              className="ml-auto space-x-2"
            >
              <PlusCircle className="mr-2 h-4 w-4" />
              Create New Secret
            </Button>
          </NewCredentialsDialogTrigger>
        </NewCredentialsDialog>
      </div>
      <Separator />

      <div className="space-y-4">
        {secrets?.length ? (
          secrets?.map((secret, idx) => (
            <SecretsTable key={idx} secret={secret} deleteFn={mutate} />
          ))
        ) : (
          <NoContent
            className="min-h-[10vh] text-sm"
            message="No credentials found"
          />
        )}
      </div>
    </div>
  )
}

function SecretsTable({
  secret,
  deleteFn,
}: {
  secret: Secret
  deleteFn: (secret: Secret) => void
}) {
  return (
    <Card className="w-full border">
      <CardHeader>
        <div className="flex items-end justify-between">
          <div className="space-y-2">
            <CardTitle>{secret.name}</CardTitle>
            <CardDescription>
              {secret.description || "No description."}
            </CardDescription>
          </div>
          <ConfirmationDialog
            title={`Delete ${secret.name}?`}
            description="Are you sure you want to delete this secret? This action cannot be undone."
            onConfirm={() => deleteFn(secret)}
          >
            <Button
              size="sm"
              variant="ghost"
              className="border border-red-500/70 bg-red-500/10 text-red-500/80 hover:bg-red-500/20 hover:text-red-500"
            >
              <Trash2Icon className="size-3.5" />
            </Button>
          </ConfirmationDialog>
        </div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow className="grid h-6 grid-cols-2 text-xs">
              <TableHead className="col-span-1">Key</TableHead>
              <TableHead className="col-span-1">Value</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {secret.keys.map(({ key }, idx) => (
              <TableRow key={idx} className="grid grid-cols-2">
                <TableCell className="col-span-1">
                  <Label htmlFor="stock-1" className="sr-only">
                    Key
                  </Label>
                  <span className="text-sm">{key}</span>
                </TableCell>
                <TableCell className="col-span-1">
                  <Label htmlFor="price-1" className="sr-only">
                    Value
                  </Label>
                  <Input
                    className="border-none p-0 text-sm shadow-none"
                    type="password"
                    value="DUMMY_VALUE"
                    readOnly
                    disabled
                  />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

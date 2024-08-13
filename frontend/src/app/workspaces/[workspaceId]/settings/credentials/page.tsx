"use client"

import { SecretResponse } from "@/client"
import { PlusCircle, Trash2Icon } from "lucide-react"

import { useSecrets } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Separator } from "@/components/ui/separator"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/new-credential-dialog"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

export default function CredentialsPage() {
  const { secrets, secretsIsLoading, secretsError } = useSecrets()
  if (secretsIsLoading) {
    return <CenteredSpinner />
  }
  if (secretsError) {
    return <AlertNotification level="error" message={secretsError.message} />
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
              <PlusCircle className="mr-2 size-4" />
              Create new secret
            </Button>
          </NewCredentialsDialogTrigger>
        </NewCredentialsDialog>
      </div>
      <Separator />

      <div className="space-y-4">
        {secrets ? (
          secrets.map((secret, idx) => (
            <SecretsTable key={idx} secret={secret} />
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

function SecretsTable({ secret }: { secret: SecretResponse }) {
  const { deleteSecret } = useSecrets()
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
            onConfirm={async () => await deleteSecret(secret)}
          >
            <Button size="sm" variant="ghost">
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
            </TableRow>
          </TableHeader>
          <TableBody>
            {secret.keys.map((key, idx) => (
              <TableRow key={idx}>
                <TableCell className="col-span-1">
                  <Label htmlFor="stock-1" className="sr-only">
                    Key
                  </Label>
                  <span className="text-sm">{key}</span>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  )
}

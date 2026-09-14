"use client"

import { PlusIcon, Trash2Icon } from "lucide-react"
import * as React from "react"
import type { SecretStoreRead } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import { useOrgSecretStores } from "@/hooks/use-secret-stores"
import { useWorkspaceManager } from "@/lib/hooks"

/**
 * Builds the IAM trust policy an org admin attaches to the store role so the
 * Tracecat principal can assume it with the persisted external ID.
 */
export function buildStoreTrustPolicy(store: SecretStoreRead): string {
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Principal: {
            AWS: store.tracecat_aws_principal_arn ?? "<tracecat-principal-arn>",
          },
          Action: "sts:AssumeRole",
          Condition: {
            StringEquals: { "sts:ExternalId": store.external_id },
          },
        },
      ],
    },
    null,
    2
  )
}

/**
 * Minimal read-only permissions the store role needs. No ListSecrets,
 * write, delete, or rotation permissions are requested.
 */
export function buildStorePermissionPolicy(store: SecretStoreRead): string {
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:aws:secretsmanager:${store.region}:*:secret:*`,
        },
        {
          Effect: "Allow",
          Action: ["kms:Decrypt"],
          Resource: "*",
          Condition: {
            StringEquals: {
              "kms:ViaService": `secretsmanager.${store.region}.amazonaws.com`,
            },
          },
        },
      ],
    },
    null,
    2
  )
}

/** Organization settings for external AWS Secrets Manager stores. */
export function OrgSettingsSecretStores() {
  const { stores, isLoading, error } = useOrgSecretStores()

  if (isLoading) {
    return <CenteredSpinner />
  }
  if (error) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading secret stores: ${error.message}`}
      />
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Tracecat assumes the store role with its own workload identity and a
          persisted external ID. Secret values are read at runtime and never
          stored in Tracecat.
        </p>
        <CreateSecretStoreDialog />
      </div>
      {!stores || stores.length === 0 ? (
        <div className="rounded-lg border p-4 text-sm text-muted-foreground">
          No external secret stores configured yet.
        </div>
      ) : (
        <div className="space-y-4">
          {stores.map((store) => (
            <SecretStoreCard key={store.id} store={store} />
          ))}
        </div>
      )}
    </div>
  )
}

function CreateSecretStoreDialog() {
  const { createStore, createStorePending } = useOrgSecretStores()
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState("")
  const [roleArn, setRoleArn] = React.useState("")
  const [region, setRegion] = React.useState("")
  const [enabled, setEnabled] = React.useState(true)

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    await createStore({
      name: name.trim(),
      role_arn: roleArn.trim(),
      region: region.trim(),
      enabled,
    })
    setOpen(false)
    setName("")
    setRoleArn("")
    setRegion("")
    setEnabled(true)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <PlusIcon className="mr-2 size-4" />
          Add AWS store
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add AWS Secrets Manager store</DialogTitle>
          <DialogDescription>
            Provide the IAM role Tracecat should assume. The external ID is
            generated after saving and shown in the trust policy.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="store-name">Name</Label>
            <Input
              id="store-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="production-secrets"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="store-role-arn">Role ARN</Label>
            <Input
              id="store-role-arn"
              value={roleArn}
              onChange={(e) => setRoleArn(e.target.value)}
              placeholder="arn:aws:iam::123456789012:role/tracecat-secrets-reader"
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="store-region">Region</Label>
            <Input
              id="store-region"
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              placeholder="us-east-1"
              required
            />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div className="space-y-0.5">
              <Label htmlFor="store-enabled">Enabled</Label>
              <p className="text-xs text-muted-foreground">
                Disabled stores fail all runtime resolutions.
              </p>
            </div>
            <Switch
              id="store-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>
          <DialogFooter>
            <Button type="submit" disabled={createStorePending}>
              Save store
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function SecretStoreCard({ store }: { store: SecretStoreRead }) {
  const { updateStore, deleteStore, authorizeWorkspace, revokeWorkspace } =
    useOrgSecretStores()
  const { workspaces } = useWorkspaceManager()
  const [selectedWorkspaceId, setSelectedWorkspaceId] = React.useState("")

  const authorizedIds = new Set(store.authorized_workspace_ids ?? [])
  const authorizedWorkspaces = (workspaces ?? []).filter((ws) =>
    authorizedIds.has(ws.id)
  )
  const unauthorizedWorkspaces = (workspaces ?? []).filter(
    (ws) => !authorizedIds.has(ws.id)
  )
  const referenceCount = store.reference_count ?? 0

  return (
    <div className="space-y-4 rounded-lg border p-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium">{store.name}</p>
            <Badge variant="outline">AWS Secrets Manager</Badge>
            <Badge variant={store.enabled ? "secondary" : "outline"}>
              {store.enabled ? "Enabled" : "Disabled"}
            </Badge>
          </div>
          <p className="break-all font-mono text-xs text-muted-foreground">
            {store.role_arn}
          </p>
          <p className="text-xs text-muted-foreground">
            Region {store.region} · {referenceCount} secret reference
            {referenceCount === 1 ? "" : "s"}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Label
              htmlFor={`store-enabled-${store.id}`}
              className="text-xs text-muted-foreground"
            >
              Enabled
            </Label>
            <Switch
              id={`store-enabled-${store.id}`}
              checked={store.enabled}
              onCheckedChange={(checked) =>
                updateStore({ storeId: store.id, params: { enabled: checked } })
              }
            />
          </div>
          <Button
            size="sm"
            variant="ghost"
            disabled={referenceCount > 0}
            title={
              referenceCount > 0
                ? "Remove all secret references before deleting this store"
                : "Delete store"
            }
            onClick={() => deleteStore(store.id)}
          >
            <Trash2Icon className="size-4" />
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <Label className="text-xs">Trust policy</Label>
          <p className="text-xs text-muted-foreground">
            Attach to the role. External ID:{" "}
            <span className="font-mono">{store.external_id}</span>
          </p>
          <Textarea
            readOnly
            className="h-48 font-mono text-xs"
            value={buildStoreTrustPolicy(store)}
          />
        </div>
        <div className="space-y-2">
          <Label className="text-xs">Permissions policy</Label>
          <p className="text-xs text-muted-foreground">
            Read-only. Scope the resource ARNs down to the secrets you intend to
            share.
          </p>
          <Textarea
            readOnly
            className="h-48 font-mono text-xs"
            value={buildStorePermissionPolicy(store)}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label className="text-xs">Authorized workspaces</Label>
        <div className="flex flex-wrap gap-2">
          {authorizedWorkspaces.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No workspaces can reference this store yet.
            </p>
          )}
          {authorizedWorkspaces.map((ws) => (
            <Badge key={ws.id} variant="secondary" className="gap-1">
              {ws.name}
              <button
                type="button"
                aria-label={`Revoke ${ws.name}`}
                className="ml-1 text-muted-foreground hover:text-foreground"
                onClick={() =>
                  revokeWorkspace({ storeId: store.id, workspaceId: ws.id })
                }
              >
                ×
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={selectedWorkspaceId}
            onValueChange={setSelectedWorkspaceId}
          >
            <SelectTrigger className="w-64 text-sm">
              <SelectValue placeholder="Select a workspace" />
            </SelectTrigger>
            <SelectContent>
              {unauthorizedWorkspaces.map((ws) => (
                <SelectItem key={ws.id} value={ws.id}>
                  {ws.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="outline"
            disabled={!selectedWorkspaceId}
            onClick={async () => {
              await authorizeWorkspace({
                storeId: store.id,
                workspaceId: selectedWorkspaceId,
              })
              setSelectedWorkspaceId("")
            }}
          >
            Authorize
          </Button>
        </div>
      </div>
    </div>
  )
}

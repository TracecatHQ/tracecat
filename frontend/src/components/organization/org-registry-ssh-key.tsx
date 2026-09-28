"use client"

import {
  KeyRoundIcon,
  MoreHorizontalIcon,
  RefreshCcw,
  Trash2Icon,
} from "lucide-react"
import { type ReactNode, useState } from "react"
import type { SecretCreate, SecretReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Spinner } from "@/components/loading/spinner"
import {
  CreateSSHKeyDialog,
  CreateSSHKeyDialogTrigger,
} from "@/components/ssh-keys/ssh-key-create-dialog"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { useOrgSecrets } from "@/lib/hooks"

/** Mirrors `REGISTRY_GIT_SSH_KEY_SECRET_NAME` in `tracecat/registry/constants.py`. */
const REGISTRY_SSH_KEY_NAME = "github-ssh-key"
/** Registry sync reads the key from the default environment only. */
const REGISTRY_SSH_KEY_ENVIRONMENT = "default"

function isRegistryKey(secret: SecretReadMinimal): boolean {
  return (
    secret.name === REGISTRY_SSH_KEY_NAME &&
    secret.environment === REGISTRY_SSH_KEY_ENVIRONMENT
  )
}

/**
 * SSH key section for the custom registry Repository page.
 *
 * Lists the organization's SSH-key secrets, marks the one registry sync
 * uses, and lets admins add, replace, or remove keys. Independent of the
 * surrounding Git settings form: every action calls the secrets API directly.
 */
export function OrgRegistrySshKeySection() {
  const canCreate = useScopeCheck("org:secret:create") === true
  const canDelete = useScopeCheck("org:secret:delete") === true
  const {
    orgSSHKeys,
    orgSSHKeysIsLoading,
    orgSSHKeysError,
    createSecret,
    deleteSecretById,
  } = useOrgSecrets()
  const [keyPendingRemove, setKeyPendingRemove] =
    useState<SecretReadMinimal | null>(null)
  const [replaceOpen, setReplaceOpen] = useState(false)

  const sshKeys = (orgSSHKeys ?? []).filter(
    (secret) => secret.type === "ssh_key"
  )
  const registryKey = sshKeys.find(isRegistryKey) ?? null

  // The backend makes SSH key secrets write-once, so replacing is
  // delete-then-create. Rethrow so the dialog stays open for a retry.
  async function handleCreate(params: SecretCreate) {
    let removedExisting = false
    try {
      if (registryKey) {
        await deleteSecretById(registryKey)
        removedExisting = true
      }
      await createSecret(params)
    } catch (error) {
      console.error("Failed to save SSH key", error)
      if (removedExisting) {
        toast({
          title: "SSH key removed but the new key was not saved",
          description:
            "Add an SSH key so Tracecat can sync the repository again.",
          variant: "destructive",
        })
      }
      throw error
    }
  }

  async function handleConfirmRemove() {
    if (!keyPendingRemove) {
      return
    }
    const secret = keyPendingRemove
    setKeyPendingRemove(null)
    try {
      await deleteSecretById(secret)
    } catch (error) {
      console.error("Failed to remove SSH key", error)
    }
  }

  // Replacing deletes the existing key, so it needs both scopes.
  const showAdd = canCreate && registryKey === null
  const showReplace = canCreate && canDelete && registryKey !== null
  const sshKeyFieldConfig = {
    name: { defaultValue: REGISTRY_SSH_KEY_NAME, disabled: true },
    environment: {
      defaultValue: REGISTRY_SSH_KEY_ENVIRONMENT,
      disabled: true,
    },
  }

  let body: ReactNode
  if (orgSSHKeysIsLoading) {
    body = (
      <div className="flex items-center justify-center py-6">
        <Spinner className="size-4" />
      </div>
    )
  } else if (orgSSHKeysError) {
    body = (
      <p className="px-3 py-6 text-center text-sm text-destructive">
        {getApiErrorDetail(orgSSHKeysError) ?? "Couldn't load SSH keys."}
      </p>
    )
  } else if (sshKeys.length === 0) {
    body = (
      <div className="flex items-center gap-3 px-3 py-2.5">
        <KeyRoundIcon className="size-4 shrink-0 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">No SSH key added</p>
      </div>
    )
  } else {
    body = (
      <ul className="divide-y">
        {sshKeys.map((secret) => {
          const isRegistry = isRegistryKey(secret)
          const canReplaceThis = showReplace && isRegistry
          return (
            <li key={secret.id} className="flex items-center gap-3 px-3 py-2.5">
              <KeyRoundIcon className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-0 flex-1 space-y-0.5">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate font-mono text-sm">
                    {secret.name}
                  </span>
                  {isRegistry && (
                    <Badge variant="secondary" className="text-xs">
                      In use
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {secret.environment}
                </p>
              </div>
              {(canDelete || canReplaceThis) && (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      aria-label="SSH key actions"
                    >
                      <MoreHorizontalIcon className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    {canReplaceThis && (
                      <DropdownMenuItem onSelect={() => setReplaceOpen(true)}>
                        <RefreshCcw className="mr-2 size-4" />
                        Replace SSH key
                      </DropdownMenuItem>
                    )}
                    {canDelete && (
                      <DropdownMenuItem
                        onSelect={() => setKeyPendingRemove(secret)}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2Icon className="mr-2 size-4" />
                        Remove
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <div className="space-y-2">
      <div className="space-y-1">
        <div className="flex items-center justify-between gap-4">
          <p className="text-sm font-medium leading-none">SSH key</p>
          {showAdd && (
            <CreateSSHKeyDialog
              handler={handleCreate}
              title="Add SSH key"
              description="Paste the private key. Tracecat uses it to clone the custom registry repository."
              submitLabel="Add SSH key"
              fieldConfig={sshKeyFieldConfig}
            >
              <CreateSSHKeyDialogTrigger asChild>
                <Button type="button" variant="outline" size="sm">
                  Add SSH key
                </Button>
              </CreateSSHKeyDialogTrigger>
            </CreateSSHKeyDialog>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          Tracecat uses this key to clone the repository. Add the public key to
          the repository as a read-only deploy key.
        </p>
      </div>
      <div className="rounded-md border">{body}</div>

      {showReplace && (
        <CreateSSHKeyDialog
          open={replaceOpen}
          onOpenChange={setReplaceOpen}
          handler={handleCreate}
          title="Replace SSH key"
          description="Paste the new private key. Tracecat removes the current key when you save."
          submitLabel="Replace SSH key"
          fieldConfig={sshKeyFieldConfig}
        />
      )}

      <AlertDialog
        open={keyPendingRemove !== null}
        onOpenChange={(open) => {
          if (!open) {
            setKeyPendingRemove(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove SSH key?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently removes{" "}
              <span className="font-mono">{keyPendingRemove?.name ?? ""}</span>.
              {keyPendingRemove && isRegistryKey(keyPendingRemove)
                ? " Tracecat can't sync the repository until you add a new key."
                : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleConfirmRemove}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Remove key
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

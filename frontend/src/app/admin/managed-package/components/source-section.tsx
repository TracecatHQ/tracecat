"use client"

import { type FormEvent, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { toastApiError, UUID_PATTERN } from "../managed-package-utils"
import { useManagedSource } from "../use-managed-package"

/** Shows the source workspace and lets an admin point it at another one. */
export function SourceSection() {
  const { source, isLoading, error, setSource, setSourcePending } =
    useManagedSource()
  const [workspaceId, setWorkspaceId] = useState("")
  const [inputError, setInputError] = useState<string | null>(null)

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = workspaceId.trim()
    if (!UUID_PATTERN.test(value)) {
      setInputError("Enter a workspace ID (UUID).")
      return
    }
    setInputError(null)
    try {
      await setSource(value)
      setWorkspaceId("")
      toast({
        title: "Source updated",
        description: "The managed package now publishes from this workspace.",
      })
    } catch (err) {
      toastApiError("Failed to set source", err)
    }
  }

  return (
    <section className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold">Source</h3>
        <p className="text-sm text-muted-foreground">
          The workspace the package is published from, and the Git repository it
          is published to.
        </p>
      </div>

      {isLoading && (
        <div className="text-sm text-muted-foreground">Loading source...</div>
      )}
      {!isLoading && error && (
        <div className="text-sm text-destructive">
          {getApiErrorDetail(error) ?? "Failed to load the source."}
        </div>
      )}
      {!isLoading && !error && !source && (
        <div className="text-sm text-muted-foreground">
          No source workspace is configured.
        </div>
      )}
      {source && (
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 rounded-md border p-4 text-sm">
          <dt className="text-muted-foreground">Workspace ID</dt>
          <dd className="font-mono text-xs leading-5">{source.workspace_id}</dd>
          <dt className="text-muted-foreground">Organization ID</dt>
          <dd className="font-mono text-xs leading-5">
            {source.organization_id}
          </dd>
          <dt className="text-muted-foreground">Repository</dt>
          <dd className="break-all font-mono text-xs leading-5">
            {source.git_repo_url}
          </dd>
          <dt className="text-muted-foreground">Provider</dt>
          <dd>{source.git_provider}</dd>
        </dl>
      )}

      <form onSubmit={handleSubmit} className="space-y-2">
        <Label htmlFor="managed-source-workspace-id">
          {source ? "Change source workspace" : "Source workspace"}
        </Label>
        <div className="flex items-start gap-2">
          <Input
            id="managed-source-workspace-id"
            value={workspaceId}
            onChange={(event) => setWorkspaceId(event.target.value)}
            placeholder="Workspace ID"
            className="max-w-md font-mono text-xs"
          />
          <Button
            type="submit"
            size="sm"
            className="h-9"
            disabled={setSourcePending || workspaceId.trim() === ""}
          >
            {setSourcePending ? "Saving..." : "Save"}
          </Button>
        </div>
        {inputError && <p className="text-xs text-destructive">{inputError}</p>}
        <p className="text-xs text-muted-foreground">
          The workspace must have a Git repository configured for workspace
          sync.
        </p>
      </form>
    </section>
  )
}

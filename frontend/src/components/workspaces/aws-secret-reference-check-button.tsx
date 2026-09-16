"use client"

import { CheckCircle2Icon, XCircleIcon } from "lucide-react"
import React from "react"
import type { SecretReferenceCheckResult } from "@/client"
import { Button } from "@/components/ui/button"
import { useAwsSecretReferences } from "@/hooks/use-secret-stores"

interface AwsSecretReferenceCheckButtonProps {
  workspaceId: string
  secretId: string
}

/**
 * Runs a server-side reference check for an AWS-backed secret. The result is
 * only a sanitized ok/failure plus resolved key names; the browser never sees
 * the remote value.
 */
export function AwsSecretReferenceCheckButton({
  workspaceId,
  secretId,
}: AwsSecretReferenceCheckButtonProps) {
  const { checkReference, checkReferencePending } =
    useAwsSecretReferences(workspaceId)
  const [result, setResult] = React.useState<SecretReferenceCheckResult | null>(
    null
  )

  async function handleCheck(event: React.MouseEvent) {
    event.stopPropagation()
    try {
      setResult(await checkReference(secretId))
    } catch (error) {
      setResult({
        ok: false,
        message: error instanceof Error ? error.message : "Check failed",
      })
    }
  }

  let status: React.ReactNode = null
  if (result?.ok) {
    status = (
      <span
        className="flex items-center gap-1 text-[11px] text-muted-foreground"
        title={`Resolved keys: ${(result.resolved_keys ?? []).join(", ")}`}
      >
        <CheckCircle2Icon className="size-3.5 text-green-600" />
        Reachable
      </span>
    )
  } else if (result) {
    status = (
      <span
        className="flex items-center gap-1 text-[11px] text-muted-foreground"
        title={result.message ?? undefined}
      >
        <XCircleIcon className="size-3.5 text-destructive" />
        {result.error_code ?? "Failed"}
      </span>
    )
  }

  return (
    <>
      {status}
      <Button
        variant="outline"
        size="sm"
        className="h-6 border-input bg-background px-2.5 text-[11px] text-foreground hover:bg-muted"
        disabled={checkReferencePending}
        onClick={handleCheck}
      >
        {checkReferencePending ? "Checking…" : "Check"}
      </Button>
    </>
  )
}

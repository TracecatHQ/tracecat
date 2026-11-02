"use client"

import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react"
import React from "react"
import {
  ApiError,
  type AwsSecretResolutionErrorCode,
  type SecretReferenceCheckResult,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  describeApiError,
  useAwsSecretReferences,
} from "@/hooks/use-secret-stores"
import { cn } from "@/lib/utils"

/** Short label and fix hint for each sanitized check failure code. */
export const CHECK_ERROR_MESSAGES: Record<
  AwsSecretResolutionErrorCode,
  { label: string; hint: string }
> = {
  store_disabled: {
    label: "Store disabled",
    hint: "The secret store is disabled. Ask an organization admin to enable it.",
  },
  store_not_authorized: {
    label: "Store not authorized",
    hint: "This workspace is not authorized to use the secret store. Ask an organization admin to authorize it.",
  },
  assume_role_failed: {
    label: "Cannot assume role",
    hint: "Tracecat could not assume the store's IAM role. Check that the role's trust policy allows the Tracecat principal and includes the store's external ID.",
  },
  access_denied: {
    label: "Access denied",
    hint: "The store's IAM role cannot read this secret. Allow secretsmanager:GetSecretValue on the secret, and kms:Decrypt if it uses a customer managed key.",
  },
  not_found: {
    label: "Secret not found",
    hint: "No secret with this name or ARN exists in the store's region. Check the spelling and region.",
  },
  decryption_failed: {
    label: "Decryption failed",
    hint: "AWS could not decrypt the secret. Allow kms:Decrypt on the secret's KMS key for the store's IAM role.",
  },
  throttled: {
    label: "Rate limited",
    hint: "AWS throttled the request. Try again in a moment.",
  },
  timeout: {
    label: "Timed out",
    hint: "AWS did not respond in time. Try again in a moment.",
  },
  binary_value: {
    label: "Binary secret",
    hint: "The secret stores a binary value. Only text (SecretString) values are supported.",
  },
  malformed_json: {
    label: "Not JSON",
    hint: "The secret value is not a JSON object. Use the whole string mapping, or store the value as a JSON object.",
  },
  missing_field: {
    label: "Missing JSON field",
    hint: "A mapped JSON field is missing from the secret value. Check the field names in the mapping.",
  },
  non_string_field: {
    label: "Field not a string",
    hint: "A mapped JSON field is not a string. Only top-level string fields can be mapped.",
  },
  invalid_mapping: {
    label: "Request rejected",
    hint: "AWS rejected the request. The secret may be scheduled for deletion, or the name or ARN is invalid.",
  },
  region_mismatch: {
    label: "Region mismatch",
    hint: "The secret ARN is in a different region than the store. Use a secret from the store's region.",
  },
  unknown: {
    label: "Check failed",
    hint: "The check failed for an unknown reason. Try again, or contact support if it continues.",
  },
}

/** Result of the most recent check, held only for this page view. */
export type AwsReferenceCheckState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ok"; resolvedKeys: string[] }
  | { status: "failed"; label: string; hint: string; detail: string | null }

/**
 * Runs a server-side reference check for an AWS-backed secret. The result is
 * only a sanitized ok/failure plus resolved key names; the browser never sees
 * the remote value.
 */
export function useAwsSecretReferenceCheck(
  workspaceId: string,
  secretId: string
) {
  const { checkReference } = useAwsSecretReferences(workspaceId)
  const [state, setState] = React.useState<AwsReferenceCheckState>({
    status: "idle",
  })

  async function run() {
    setState({ status: "checking" })
    let result: SecretReferenceCheckResult
    try {
      result = await checkReference(secretId)
    } catch (error) {
      result = {
        ok: false,
        message:
          error instanceof ApiError
            ? describeApiError(error)
            : "The check could not run. Try again.",
      }
    }
    if (result.ok) {
      setState({ status: "ok", resolvedKeys: result.resolved_keys ?? [] })
      return
    }
    const failure = result.error_code
      ? CHECK_ERROR_MESSAGES[result.error_code]
      : null
    setState({
      status: "failed",
      label: failure?.label ?? "Check failed",
      hint: failure?.hint ?? result.message ?? "The check could not run.",
      // The API message carries the AWS error code; keep it for troubleshooting.
      detail: failure ? (result.message ?? null) : null,
    })
  }

  return { state, run }
}

/** Check state and runner for one AWS-backed secret. */
export type AwsSecretReferenceCheck = ReturnType<
  typeof useAwsSecretReferenceCheck
>

/** Gives a list row its own check state without a component per row type. */
export function AwsSecretReferenceCheckScope({
  workspaceId,
  secretId,
  children,
}: {
  workspaceId: string
  secretId: string
  children: (check: AwsSecretReferenceCheck) => React.ReactNode
}) {
  return children(useAwsSecretReferenceCheck(workspaceId, secretId))
}

/** One button for the check and its result. Clicking it checks again. */
export function AwsSecretReferenceCheckButton({
  state,
  onCheck,
}: {
  state: AwsReferenceCheckState
  onCheck: () => void
}) {
  let label = "Check access"
  let icon: React.ReactNode = null
  let title = "Check that Tracecat can read this secret"
  if (state.status === "checking") {
    label = "Checking…"
    icon = <Loader2Icon className="size-3.5 animate-spin" />
  } else if (state.status === "ok") {
    label = "Reachable"
    icon = <CheckCircle2Icon className="size-3.5 text-green-600" />
    title = `Resolved keys: ${state.resolvedKeys.join(", ")}. Click to check again.`
  } else if (state.status === "failed") {
    label = state.label
    icon = <XCircleIcon className="size-3.5 text-destructive" />
    title = "Click to check again."
  }
  return (
    <Button
      variant="outline"
      size="sm"
      className="h-6 gap-1 border-input bg-background px-2.5 text-[11px] text-foreground hover:bg-muted"
      disabled={state.status === "checking"}
      title={title}
      onClick={(event) => {
        event.stopPropagation()
        onCheck()
      }}
    >
      {icon}
      {label}
    </Button>
  )
}

/** The fix for a failed check, shown under the row. */
export function AwsSecretReferenceCheckHint({
  state,
  className,
}: {
  state: AwsReferenceCheckState
  className?: string
}) {
  if (state.status !== "failed") return null
  return (
    <p
      className={cn("text-xs text-muted-foreground", className)}
      title={state.detail ?? undefined}
    >
      {state.hint}
    </p>
  )
}

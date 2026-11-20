"use client"

import { CheckCircle2Icon, Loader2Icon, XCircleIcon } from "lucide-react"
import React from "react"
import {
  ApiError,
  type SecretReferenceCheckResult,
  type SecretResolutionErrorCode,
  type SecretSource,
} from "@/client"
import { providerForSource } from "@/components/organization/secret-store-providers"
import { Button } from "@/components/ui/button"
import {
  describeApiError,
  useSecretReferences,
} from "@/hooks/use-secret-stores"
import { cn } from "@/lib/utils"

/**
 * Short label and provider-neutral fix hint for each sanitized check failure
 * code. A provider entry's `reference.checkHints` replaces the hint.
 */
export const CHECK_ERROR_MESSAGES: Record<
  SecretResolutionErrorCode,
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
    hint: "Tracecat could not assume the store's role. Check the store's setup.",
  },
  access_denied: {
    label: "Access denied",
    hint: "The store's credentials cannot read this secret. Grant read access to it in the store.",
  },
  not_found: {
    label: "Secret not found",
    hint: "No secret with this reference exists in the store, or the store's credentials cannot see it.",
  },
  decryption_failed: {
    label: "Decryption failed",
    hint: "The store could not decrypt the secret.",
  },
  throttled: {
    label: "Rate limited",
    hint: "The store throttled the request. Try again in a moment.",
  },
  timeout: {
    label: "Timed out",
    hint: "The store did not respond in time. Try again in a moment.",
  },
  binary_value: {
    label: "Binary secret",
    hint: "The secret stores a binary value. Only text values are supported.",
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
    hint: "The store rejected the request. Check the secret reference.",
  },
  region_mismatch: {
    label: "Region mismatch",
    hint: "The secret is in a different region than the store.",
  },
  unknown: {
    label: "Check failed",
    hint: "The check failed for an unknown reason. Try again, or contact support if it continues.",
  },
  auth_failed: {
    label: "Sign-in failed",
    hint: "The store rejected its credentials. Ask an organization admin to check the store's credential secret.",
  },
  unavailable: {
    label: "Store unavailable",
    hint: "The store could not be reached or returned an error. Try again in a moment.",
  },
  invalid_reference: {
    label: "Invalid reference",
    hint: "The secret reference is not in the format this store expects.",
  },
  credential_missing: {
    label: "Credential missing",
    hint: "The store's credential secret is missing a required key. Ask an organization admin to fix the store's setup.",
  },
}

/** Result of the most recent check, held only for this page view. */
export type SecretReferenceCheckState =
  | { status: "idle" }
  | { status: "checking" }
  | { status: "ok"; resolvedKeys: string[] }
  | { status: "failed"; label: string; hint: string; detail: string | null }

/**
 * Runs a server-side reference check for an externally backed secret. The
 * result is only a sanitized ok/failure plus resolved key names; the browser
 * never sees the remote value.
 */
export function useSecretReferenceCheck(
  workspaceId: string,
  secretId: string,
  source?: SecretSource | null
) {
  const { checkReference } = useSecretReferences(workspaceId)
  const [state, setState] = React.useState<SecretReferenceCheckState>({
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
    const providerHint = result.error_code
      ? providerForSource(source)?.reference.checkHints[result.error_code]
      : undefined
    setState({
      status: "failed",
      label: failure?.label ?? "Check failed",
      hint:
        providerHint ??
        failure?.hint ??
        result.message ??
        "The check could not run.",
      // The API message carries the provider error code; keep it for troubleshooting.
      detail: failure ? (result.message ?? null) : null,
    })
  }

  return { state, run }
}

/** Check state and runner for one externally backed secret. */
export type SecretReferenceCheck = ReturnType<typeof useSecretReferenceCheck>

/** Gives a list row its own check state without a component per row type. */
export function SecretReferenceCheckScope({
  workspaceId,
  secretId,
  source,
  children,
}: {
  workspaceId: string
  secretId: string
  source?: SecretSource | null
  children: (check: SecretReferenceCheck) => React.ReactNode
}) {
  return children(useSecretReferenceCheck(workspaceId, secretId, source))
}

/** One button for the check and its result. Clicking it checks again. */
export function SecretReferenceCheckButton({
  state,
  onCheck,
}: {
  state: SecretReferenceCheckState
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
export function SecretReferenceCheckHint({
  state,
  className,
}: {
  state: SecretReferenceCheckState
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

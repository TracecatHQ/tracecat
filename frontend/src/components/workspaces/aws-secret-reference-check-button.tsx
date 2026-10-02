"use client"

import { CheckCircle2Icon, XCircleIcon } from "lucide-react"
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
        message:
          error instanceof ApiError
            ? describeApiError(error)
            : "The check could not run. Try again.",
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
    const failure = result.error_code
      ? CHECK_ERROR_MESSAGES[result.error_code]
      : null
    // The API message carries the AWS error code; keep it for troubleshooting.
    const title = [failure?.hint, result.message].filter(Boolean).join("\n\n")
    status = (
      <span
        className="flex items-center gap-1 text-[11px] text-muted-foreground"
        title={title || undefined}
      >
        <XCircleIcon className="size-3.5 text-destructive" />
        {failure?.label ?? "Check failed"}
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

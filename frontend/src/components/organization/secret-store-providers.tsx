"use client"

import type * as React from "react"
import type {
  SecretStoreCreate,
  SecretStoreProvider,
  SecretStoreRead,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"

/** Draft provider config held by the create dialog before submission. */
export type CreateConfigState = Record<string, string>

/** Provider-specific labels, fields, and detail rendering for a secret store. */
export type SecretStoreProviderEntry = {
  label: string
  summary: (store: SecretStoreRead) => string
  createTitle: string
  createDescription: string
  CreateFields: React.ComponentType<{
    config: CreateConfigState
    onChange: (next: CreateConfigState) => void
  }>
  toCreateConfig: (config: CreateConfigState) => SecretStoreCreate["config"]
  Details: React.ComponentType<{ store: SecretStoreRead }>
}

/**
 * Builds the IAM trust policy an org admin attaches to the store role so the
 * Tracecat principal can assume it with the persisted external ID.
 */
function buildStoreTrustPolicy(store: SecretStoreRead): string {
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
            StringEquals: { "sts:ExternalId": store.config.external_id },
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
  const partition = store.config.role_arn.split(":")[1]
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:${partition}:secretsmanager:${store.config.region}:*:secret:*`,
        },
        {
          Effect: "Allow",
          Action: ["kms:Decrypt"],
          Resource: "*",
          Condition: {
            StringEquals: {
              // KMS ViaService uses amazonaws.com in every partition, including China.
              // https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html#conditions-kms-via-service
              "kms:ViaService": `secretsmanager.${store.config.region}.amazonaws.com`,
            },
          },
        },
      ],
    },
    null,
    2
  )
}

function AwsSecretsManagerCreateFields({
  config,
  onChange,
}: {
  config: CreateConfigState
  onChange: (next: CreateConfigState) => void
}) {
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="store-role-arn">Role ARN</Label>
        <Input
          id="store-role-arn"
          value={config.role_arn ?? ""}
          onChange={(e) => onChange({ ...config, role_arn: e.target.value })}
          placeholder="arn:aws:iam::123456789012:role/tracecat-secrets-reader"
          required
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="store-region">Region</Label>
        <Input
          id="store-region"
          value={config.region ?? ""}
          onChange={(e) => onChange({ ...config, region: e.target.value })}
          placeholder="us-east-1"
          required
        />
      </div>
    </>
  )
}

function AwsSecretsManagerDetails({ store }: { store: SecretStoreRead }) {
  const trustPolicy = buildStoreTrustPolicy(store)
  const permissionPolicy = buildStorePermissionPolicy(store)

  return (
    <>
      <dl className="space-y-4 border-b pb-5">
        <div className="space-y-1.5">
          <dt className="flex items-center gap-2 text-xs font-medium">
            Role ARN
            <CopyButton
              value={store.config.role_arn}
              toastMessage="Copied role ARN"
              tooltipMessage="Copy role ARN"
            />
          </dt>
          <dd className="break-all font-mono text-xs text-muted-foreground">
            {store.config.role_arn}
          </dd>
        </div>
        <div className="space-y-1.5">
          <dt className="flex items-center gap-2 text-xs font-medium">
            External ID
            <CopyButton
              value={store.config.external_id}
              toastMessage="Copied external ID"
              tooltipMessage="Copy external ID"
            />
          </dt>
          <dd className="break-all font-mono text-xs text-muted-foreground">
            {store.config.external_id}
          </dd>
        </div>
      </dl>
      <div className="grid gap-5 md:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label htmlFor={`trust-policy-${store.id}`} className="text-xs">
              Trust policy
            </Label>
            <CopyButton
              value={trustPolicy}
              toastMessage="Copied trust policy"
              tooltipMessage="Copy trust policy"
            />
          </div>
          <p
            id={`trust-policy-help-${store.id}`}
            className="text-xs text-muted-foreground md:min-h-8"
          >
            Attach to the role in AWS. Includes the external ID for this store.
          </p>
          <Textarea
            id={`trust-policy-${store.id}`}
            aria-describedby={`trust-policy-help-${store.id}`}
            readOnly
            className="h-48 resize-none bg-muted/30 font-mono text-xs"
            value={trustPolicy}
          />
        </div>
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <Label
              htmlFor={`permissions-policy-${store.id}`}
              className="text-xs"
            >
              Permissions policy
            </Label>
            <CopyButton
              value={permissionPolicy}
              toastMessage="Copied permissions policy"
              tooltipMessage="Copy permissions policy"
            />
          </div>
          <p
            id={`permissions-policy-help-${store.id}`}
            className="text-xs text-muted-foreground md:min-h-8"
          >
            Read-only access. Limit the resource ARNs to the secrets you want to
            share.
          </p>
          <Textarea
            id={`permissions-policy-${store.id}`}
            aria-describedby={`permissions-policy-help-${store.id}`}
            readOnly
            className="h-48 resize-none bg-muted/30 font-mono text-xs"
            value={permissionPolicy}
          />
        </div>
      </div>
    </>
  )
}

/** Provider-specific rendering and payload building, keyed by provider. */
export const SECRET_STORE_PROVIDERS: Record<
  SecretStoreProvider,
  SecretStoreProviderEntry
> = {
  aws_secrets_manager: {
    label: "AWS Secrets Manager",
    summary: (store) => `Region ${store.config.region}`,
    createTitle: "Add AWS Secrets Manager store",
    createDescription:
      "Enter the AWS role Tracecat will use to read secrets. Saving generates the external ID and trust policy.",
    CreateFields: AwsSecretsManagerCreateFields,
    toCreateConfig: (config) => ({
      provider: "aws_secrets_manager",
      role_arn: (config.role_arn ?? "").trim(),
      region: (config.region ?? "").trim(),
    }),
    Details: AwsSecretsManagerDetails,
  },
}

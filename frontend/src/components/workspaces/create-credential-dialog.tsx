"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import type { DialogProps } from "@radix-ui/react-dialog"
import {
  Braces,
  CheckCheckIcon,
  CopyIcon,
  FileKey2,
  FileTextIcon,
  InfoIcon,
  KeyRoundIcon,
  PlusCircle,
  ShieldCheck,
  Trash2Icon,
  UploadCloudIcon,
} from "lucide-react"
import React from "react"
import {
  type ArrayPath,
  type FieldPath,
  useFieldArray,
  useForm,
} from "react-hook-form"
import { z } from "zod"
import type { SecretCreate, SecretDefinition } from "@/client"
import { CreateSecretTooltip } from "@/components/secrets/create-secret-tooltip"
import { sshKeyRegex } from "@/components/ssh-keys/ssh-key-utils"
import { SshPrivateKeyField } from "@/components/ssh-keys/ssh-private-key-field"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
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
import { toast } from "@/components/ui/use-toast"
import { useAwsAssumeRoleAccess, useWorkspaceSecrets } from "@/lib/hooks"
import { cn, copyToClipboard } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const pemCertificateRegex =
  /-----BEGIN CERTIFICATE-----(?:\r?\n)[A-Za-z0-9+/=\s]+(?:\r?\n)-----END CERTIFICATE-----/

const AWS_ROLE_ARN_KEY = "AWS_ROLE_ARN"
const OKTA_SECRET_NAME = "okta"
const OKTA_MIN_DPOP_KEY_ROTATION_SECONDS = 3600
const OKTA_MAX_DPOP_KEY_ROTATION_SECONDS = 90 * 24 * 3600
const oktaPrivateKeyFileExtensionRegex = /\.(json|pem)$/i

type OktaAuthMethod = "ssws" | "bearer" | "private_key"

const oktaAuthMethodOptions: {
  value: OktaAuthMethod
  label: string
}[] = [
  {
    value: "ssws",
    label: "API token",
  },
  {
    value: "bearer",
    label: "Bearer token",
  },
  {
    value: "private_key",
    label: "Private key",
  },
]

function isOktaCredential(values: { type: string; name: string }) {
  return values.type === "custom" && values.name === OKTA_SECRET_NAME
}

function isValidOktaPrivateKeyJwk(value: string) {
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    return false
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return false
  }
  const jwk = parsed as Record<string, unknown>
  // Okta private-key auth only supports RSA, and signing requires the private
  // exponent `d` plus the public params `n`/`e`. A public-only or non-JWK
  // object parses as JSON but fails inside the Okta SDK at action-run time.
  const hasField = (field: string) =>
    typeof jwk[field] === "string" && (jwk[field] as string).length > 0
  return jwk.kty === "RSA" && hasField("n") && hasField("e") && hasField("d")
}

function isValidOktaPrivateKey(value: string | undefined) {
  const trimmed = value?.trim()
  if (!trimmed) {
    return false
  }
  if (trimmed.startsWith("{")) {
    return isValidOktaPrivateKeyJwk(trimmed)
  }
  return sshKeyRegex.test(trimmed)
}

function buildAwsTrustPolicy(args: {
  tracecatAwsPrincipalArn: string
  externalId: string
}) {
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Principal: {
            AWS: args.tracecatAwsPrincipalArn,
          },
          Action: "sts:AssumeRole",
          Condition: {
            StringEquals: {
              "sts:ExternalId": args.externalId,
            },
          },
        },
      ],
    },
    null,
    2
  )
}

function shouldSuppressAwsAssumeRoleError(error: unknown): boolean {
  if (!error || typeof error !== "object") {
    return false
  }

  const status =
    "status" in error && typeof error.status === "number" ? error.status : null
  const body =
    "body" in error && typeof error.body === "object" && error.body
      ? error.body
      : null
  const detail =
    body && "detail" in body && typeof body.detail === "string"
      ? body.detail
      : null

  return (
    status === 503 &&
    detail === "AWS AssumeRole access is not available right now."
  )
}

function highlightJson(json: string): React.ReactNode[] {
  const tokenRegex =
    /("(?:\\u[a-fA-F0-9]{4}|\\[^u]|[^\\"])*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g
  const tokens: React.ReactNode[] = []
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = tokenRegex.exec(json)) !== null) {
    if (match.index > lastIndex) {
      tokens.push(json.slice(lastIndex, match.index))
    }

    const [value] = match
    const isKey = Boolean(match[2])
    let className = "text-amber-700 dark:text-amber-300"

    if (isKey) {
      className = "text-sky-700 dark:text-sky-300"
    } else if (value.startsWith('"')) {
      className = "text-emerald-700 dark:text-emerald-300"
    } else if (match[3]) {
      className = "text-violet-700 dark:text-violet-300"
    }

    tokens.push(
      <span key={`${match.index}-${value}`} className={className}>
        {value}
      </span>
    )
    lastIndex = match.index + value.length
  }

  if (lastIndex < json.length) {
    tokens.push(json.slice(lastIndex))
  }

  return tokens
}

function JsonSyntaxBlock({ value }: { value: string }) {
  const [copied, setCopied] = React.useState(false)

  function handleCopy() {
    void copyToClipboard({
      value,
      message: "Copied trust policy.",
    })
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative overflow-hidden rounded-md border bg-muted/30">
      <Button
        type="button"
        variant="ghost"
        size="icon"
        className="absolute right-1 top-1 z-10 size-6"
        onClick={handleCopy}
        aria-label="Copy trust policy"
      >
        {copied ? (
          <CheckCheckIcon className="size-3.5" />
        ) : (
          <CopyIcon className="size-3.5" />
        )}
      </Button>
      <pre className="overflow-x-auto p-3 pr-10 font-mono text-xs leading-relaxed text-foreground">
        <code>{highlightJson(value)}</code>
      </pre>
    </div>
  )
}

const validatePemField = (
  value: string | undefined,
  ctx: z.RefinementCtx,
  path: string[],
  requiredMessage: string,
  invalidMessage: string,
  regex: RegExp
) => {
  if (!value?.trim()) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path,
      message: requiredMessage,
    })
    return
  }
  if (!regex.test(value)) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path,
      message: invalidMessage,
    })
  }
}

const createSecretSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(1, "Name is required.")
      .max(100, "Name must be 100 characters or fewer.")
      .default(""),
    description: z.string().max(255).default(""),
    environment: z
      .string()
      .nullable()
      .transform((val) => val || "default"), // "default" if null or empty
    type: z.enum(["custom", "ssh_key", "mtls", "ca_cert"]).default("custom"),
    keys: z
      .array(
        z.object({
          key: z.string().optional(),
          value: z.string().optional(),
          isOptional: z.boolean().optional(),
        })
      )
      .default([]),
    private_key: z.string().optional(),
    tls_certificate: z.string().optional(),
    tls_private_key: z.string().optional(),
    ca_certificate: z.string().optional(),
    okta_auth_method: z.enum(["ssws", "bearer", "private_key"]).default("ssws"),
    okta_base_url: z.string().optional(),
    okta_api_token: z.string().optional(),
    okta_access_token: z.string().optional(),
    okta_service_token: z.string().optional(),
    okta_client_id: z.string().optional(),
    okta_scopes: z.string().optional(),
    okta_kid: z.string().optional(),
    okta_private_key: z.string().optional(),
    okta_dpop_enabled: z.boolean().default(false),
    okta_dpop_key_rotation_interval: z.string().optional(),
  })
  .superRefine((values, ctx) => {
    if (isOktaCredential(values)) {
      if (
        values.okta_auth_method === "ssws" &&
        !values.okta_api_token?.trim()
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["okta_api_token"],
          message: "Okta API token is required.",
        })
      }
      if (
        values.okta_auth_method === "bearer" &&
        !values.okta_access_token?.trim() &&
        !values.okta_service_token?.trim()
      ) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["okta_access_token"],
          message: "Access token or service token is required.",
        })
      }
      if (values.okta_auth_method === "private_key") {
        if (!values.okta_client_id?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["okta_client_id"],
            message: "Client ID is required.",
          })
        }
        if (!values.okta_scopes?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["okta_scopes"],
            message: "Scopes are required.",
          })
        }
        if (!isValidOktaPrivateKey(values.okta_private_key)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["okta_private_key"],
            message:
              "Private key must be a PEM block or an RSA private JWK (kty, n, e, d).",
          })
        }

        const rotationInterval = values.okta_dpop_key_rotation_interval?.trim()
        if (values.okta_dpop_enabled && rotationInterval) {
          const seconds = Number(rotationInterval)
          if (!Number.isInteger(seconds)) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ["okta_dpop_key_rotation_interval"],
              message: "Rotation interval must be an integer.",
            })
          } else if (
            seconds < OKTA_MIN_DPOP_KEY_ROTATION_SECONDS ||
            seconds > OKTA_MAX_DPOP_KEY_ROTATION_SECONDS
          ) {
            ctx.addIssue({
              code: z.ZodIssueCode.custom,
              path: ["okta_dpop_key_rotation_interval"],
              message: "Rotation interval must be 3600 to 7776000 seconds.",
            })
          }
        }
      }
    } else if (values.type === "custom") {
      if (!values.keys.length) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ["keys"],
          message: "At least one key is required.",
        })
      }
      values.keys.forEach((item, index) => {
        if (!item.key?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["keys", index, "key"],
            message: "Key is required.",
          })
        }
        // Only validate value if it's not an optional field from a template
        // or if user has entered something
        if (!item.isOptional && !item.value?.trim()) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            path: ["keys", index, "value"],
            message: "Value is required.",
          })
        }
      })
    }

    if (values.type === "ssh_key") {
      validatePemField(
        values.private_key,
        ctx,
        ["private_key"],
        "SSH private key is required.",
        "Invalid SSH private key format. Must include PEM header and footer.",
        sshKeyRegex
      )
    }

    if (values.type === "mtls") {
      validatePemField(
        values.tls_certificate,
        ctx,
        ["tls_certificate"],
        "TLS certificate is required.",
        "Invalid TLS certificate format. Must include PEM header and footer.",
        pemCertificateRegex
      )
      validatePemField(
        values.tls_private_key,
        ctx,
        ["tls_private_key"],
        "TLS private key is required.",
        "Invalid TLS private key format. Must include PEM header and footer.",
        sshKeyRegex
      )
    }

    if (values.type === "ca_cert") {
      validatePemField(
        values.ca_certificate,
        ctx,
        ["ca_certificate"],
        "CA certificate is required.",
        "Invalid CA certificate format. Must include PEM header and footer.",
        pemCertificateRegex
      )
    }
  })

type CreateSecretForm = z.infer<typeof createSecretSchema>

interface CreateCredentialDialogProps extends DialogProps {
  template?: SecretDefinition | null
  onOpenChange: (open: boolean) => void
  className?: string
}

export function CreateCredentialDialog({
  template = null,
  open,
  onOpenChange,
  children,
  className,
}: CreateCredentialDialogProps) {
  const selectedTool = template
  const workspaceId = useWorkspaceId()
  const { createSecret } = useWorkspaceSecrets(workspaceId, {
    listEnabled: false,
  })
  const isAwsAssumeRoleTemplate = Boolean(
    selectedTool &&
      [
        ...(selectedTool.keys ?? []),
        ...(selectedTool.optional_keys ?? []),
      ].includes(AWS_ROLE_ARN_KEY)
  )
  const {
    awsAssumeRoleAccess,
    awsAssumeRoleAccessError,
    awsAssumeRoleAccessIsLoading,
  } = useAwsAssumeRoleAccess(workspaceId, {
    enabled: open && isAwsAssumeRoleTemplate,
  })

  // Form handling
  const methods = useForm<CreateSecretForm>({
    resolver: zodResolver(createSecretSchema),
    defaultValues: {
      name: "",
      description: "",
      environment: "",
      type: "custom",
      keys: [{ key: "", value: "" }],
      private_key: "",
      tls_certificate: "",
      tls_private_key: "",
      ca_certificate: "",
      okta_auth_method: "ssws",
      okta_base_url: "",
      okta_api_token: "",
      okta_access_token: "",
      okta_service_token: "",
      okta_client_id: "",
      okta_scopes: "",
      okta_kid: "",
      okta_private_key: "",
      okta_dpop_enabled: false,
      okta_dpop_key_rotation_interval: "",
    },
  })

  // Reset form when dialog opens
  React.useEffect(() => {
    if (!open) return
    if (selectedTool) {
      // Use the declared secret_type from the template definition, narrowed
      // to the form-supported subset (excludes github_app).
      const declaredType = selectedTool.secret_type ?? "custom"
      const formTypes = new Set<string>([
        "custom",
        "ssh_key",
        "mtls",
        "ca_cert",
      ])
      const effectiveType = (
        formTypes.has(declaredType) ? declaredType : "custom"
      ) as "custom" | "ssh_key" | "mtls" | "ca_cert"

      const initialKeys = [
        ...(selectedTool.keys || []).map((key) => ({
          key,
          value: "",
          isOptional: false,
        })),
        ...(selectedTool.optional_keys || []).map((key) => ({
          key,
          value: "",
          isOptional: true,
        })),
      ]

      methods.reset({
        name: selectedTool.name,
        description: "",
        environment: "default",
        type: effectiveType,
        keys: initialKeys.length > 0 ? initialKeys : [{ key: "", value: "" }],
        private_key: "",
        tls_certificate: "",
        tls_private_key: "",
        ca_certificate: "",
        okta_auth_method: "ssws",
        okta_base_url: "",
        okta_api_token: "",
        okta_access_token: "",
        okta_service_token: "",
        okta_client_id: "",
        okta_scopes: "",
        okta_kid: "",
        okta_private_key: "",
        okta_dpop_enabled: false,
        okta_dpop_key_rotation_interval: "",
      })
      return
    }
    methods.reset({
      name: "",
      description: "",
      environment: "",
      type: "custom",
      keys: [{ key: "", value: "" }],
      private_key: "",
      tls_certificate: "",
      tls_private_key: "",
      ca_certificate: "",
      okta_auth_method: "ssws",
      okta_base_url: "",
      okta_api_token: "",
      okta_access_token: "",
      okta_service_token: "",
      okta_client_id: "",
      okta_scopes: "",
      okta_kid: "",
      okta_private_key: "",
      okta_dpop_enabled: false,
      okta_dpop_key_rotation_interval: "",
    })
  }, [open, selectedTool, methods])

  const { control, register } = methods
  const secretType = methods.watch("type")
  const secretName = methods.watch("name")
  const isOktaCredentialForm = isOktaCredential({
    type: secretType,
    name: secretName,
  })
  const oktaAuthMethod = methods.watch("okta_auth_method")
  const oktaDpopEnabled = methods.watch("okta_dpop_enabled")
  const isTemplateSecret = Boolean(selectedTool)
  const roleArn =
    methods
      .watch("keys")
      .find((item) => item.key === AWS_ROLE_ARN_KEY)
      ?.value?.trim() ?? ""
  const awsTrustPolicy = awsAssumeRoleAccess
    ? buildAwsTrustPolicy({
        tracecatAwsPrincipalArn: awsAssumeRoleAccess.tracecat_aws_principal_arn,
        externalId: awsAssumeRoleAccess.external_id,
      })
    : ""
  const shouldHideAwsAssumeRoleNote = shouldSuppressAwsAssumeRoleError(
    awsAssumeRoleAccessError
  )
  const oktaPrivateKeyFileInputRef = React.useRef<HTMLInputElement | null>(null)
  const oktaPrivateKeyReadIdRef = React.useRef(0)
  const [oktaPrivateKeyFileName, setOktaPrivateKeyFileName] = React.useState<
    string | null
  >(null)
  const [isOktaPrivateKeyDragOver, setIsOktaPrivateKeyDragOver] =
    React.useState(false)

  const resetOktaPrivateKeyFileInput = React.useCallback(() => {
    if (oktaPrivateKeyFileInputRef.current) {
      oktaPrivateKeyFileInputRef.current.value = ""
    }
  }, [])

  React.useEffect(() => {
    if (!open) {
      return
    }

    oktaPrivateKeyReadIdRef.current += 1
    setOktaPrivateKeyFileName(null)
    setIsOktaPrivateKeyDragOver(false)
    resetOktaPrivateKeyFileInput()
  }, [open, resetOktaPrivateKeyFileInput])

  const handleOktaPrivateKeyFile = React.useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      const readId = oktaPrivateKeyReadIdRef.current + 1
      oktaPrivateKeyReadIdRef.current = readId

      if (!oktaPrivateKeyFileExtensionRegex.test(file.name)) {
        setOktaPrivateKeyFileName(null)
        methods.setValue("okta_private_key", "", {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: false,
        })
        methods.setError("okta_private_key", {
          type: "manual",
          message: "Upload a .pem or .json file.",
        })
        resetOktaPrivateKeyFileInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        if (readId !== oktaPrivateKeyReadIdRef.current) {
          return
        }

        const privateKey =
          typeof reader.result === "string" ? reader.result : ""

        methods.clearErrors("okta_private_key")
        methods.setValue("okta_private_key", privateKey, {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: true,
        })
        setOktaPrivateKeyFileName(file.name)
        resetOktaPrivateKeyFileInput()
      }
      reader.onerror = () => {
        if (readId !== oktaPrivateKeyReadIdRef.current) {
          return
        }

        setOktaPrivateKeyFileName(null)
        methods.setValue("okta_private_key", "", {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: false,
        })
        methods.setError("okta_private_key", {
          type: "manual",
          message: "Failed to read the uploaded private key file.",
        })
        resetOktaPrivateKeyFileInput()
      }

      reader.readAsText(file)
    },
    [methods, resetOktaPrivateKeyFileInput]
  )

  const handleOktaPrivateKeyInputChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      handleOktaPrivateKeyFile(event.target.files?.[0])
    },
    [handleOktaPrivateKeyFile]
  )

  const handleOktaPrivateKeyDrop = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setIsOktaPrivateKeyDragOver(false)
      handleOktaPrivateKeyFile(event.dataTransfer.files?.[0])
      event.dataTransfer.clearData()
    },
    [handleOktaPrivateKeyFile]
  )

  const handleOktaPrivateKeyDragOver = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = "copy"
      setIsOktaPrivateKeyDragOver(true)
    },
    []
  )

  const handleOktaPrivateKeyDragLeave = React.useCallback(
    (event: React.DragEvent<HTMLElement>) => {
      const relatedTarget = event.relatedTarget
      if (
        relatedTarget instanceof Node &&
        event.currentTarget.contains(relatedTarget)
      ) {
        return
      }
      setIsOktaPrivateKeyDragOver(false)
    },
    []
  )

  const handleOktaPrivateKeyFileChoose = React.useCallback(() => {
    oktaPrivateKeyFileInputRef.current?.click()
  }, [])

  const renderTextareaField = (
    name: FieldPath<CreateSecretForm>,
    label: string,
    placeholder: string
  ) => (
    <FormField
      key={name}
      control={control}
      name={name}
      render={() => (
        <FormItem>
          <FormLabel className="text-sm">{label}</FormLabel>
          <FormControl>
            <Textarea
              className="h-36 text-sm"
              placeholder={placeholder}
              {...register(name)}
            />
          </FormControl>
          <FormMessage />
        </FormItem>
      )}
    />
  )

  const buildOktaSecretKeys = (values: CreateSecretForm) => {
    const keys: { key: string; value: string }[] = []
    const addKey = (key: string, value: string | undefined) => {
      const trimmed = value?.trim()
      if (trimmed) {
        keys.push({ key, value: trimmed })
      }
    }

    addKey("OKTA_BASE_URL", values.okta_base_url)

    switch (values.okta_auth_method) {
      case "ssws":
        addKey("OKTA_API_TOKEN", values.okta_api_token)
        break
      case "bearer":
        addKey("OKTA_ACCESS_TOKEN", values.okta_access_token)
        addKey("OKTA_SERVICE_TOKEN", values.okta_service_token)
        break
      case "private_key":
        addKey("OKTA_CLIENT_ID", values.okta_client_id)
        addKey("OKTA_SCOPES", values.okta_scopes)
        addKey("OKTA_KID", values.okta_kid)
        addKey("OKTA_PRIVATE_KEY", values.okta_private_key)
        if (values.okta_dpop_enabled) {
          keys.push({ key: "OKTA_DPOP_ENABLED", value: "true" })
          addKey(
            "OKTA_DPOP_KEY_ROTATION_INTERVAL",
            values.okta_dpop_key_rotation_interval
          )
        }
        break
    }

    return keys
  }

  const onSubmit = async (values: CreateSecretForm) => {
    const { private_key, tls_certificate, tls_private_key, ca_certificate } =
      values
    const { type, keys } = values
    const secretName = selectedTool?.name ?? values.name

    let secretKeys: { key: string; value: string }[] = []

    switch (type) {
      case "ssh_key":
        secretKeys = [{ key: "PRIVATE_KEY", value: private_key || "" }]
        break
      case "mtls":
        secretKeys = [
          { key: "TLS_CERTIFICATE", value: tls_certificate || "" },
          { key: "TLS_PRIVATE_KEY", value: tls_private_key || "" },
        ]
        break
      case "ca_cert":
        secretKeys = [{ key: "CA_CERTIFICATE", value: ca_certificate || "" }]
        break
      default:
        if (secretName === OKTA_SECRET_NAME) {
          secretKeys = buildOktaSecretKeys(values)
        } else {
          // Filter out optional keys with empty values
          secretKeys = keys
            .filter((k) => !k.isOptional || (k.value && k.value.trim() !== ""))
            .map(({ key, value }) => ({
              key: key || "",
              value: value || "",
            }))
        }
        break
    }

    if (type === "custom" && secretKeys.length === 0) {
      toast({
        title: "No keys provided",
        description: "Please provide at least one key value.",
        variant: "destructive",
      })
      return
    }

    const secret: SecretCreate = {
      description: values.description,
      environment: values.environment,
      name: secretName,
      type,
      keys: secretKeys,
    }
    try {
      await createSecret(secret)
      onOpenChange(false)
      toast({
        title: "Secret created",
        description: `Secret "${secretName}" has been created successfully.`,
      })
      methods.reset()
    } catch (error) {
      console.log(error)
    }
  }

  const onValidationFailed = () => {
    console.error("Form validation failed")
    toast({
      title: "Form validation failed",
      description: "A validation error occurred while adding the new secret.",
    })
  }

  const inputKey = "keys"
  const typedKey = inputKey as FieldPath<CreateSecretForm>
  const { fields, append, remove } = useFieldArray<CreateSecretForm>({
    control,
    name: inputKey as ArrayPath<CreateSecretForm>,
  })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {children}
      <DialogContent className={`${className} max-h-[85vh] flex flex-col`}>
        <DialogHeader className="flex-shrink-0">
          <DialogTitle>
            {selectedTool
              ? `Configure ${selectedTool.name}`
              : "Create new secret"}
          </DialogTitle>
        </DialogHeader>

        <CreateSecretTooltip />
        <Form {...methods}>
          <form
            onSubmit={methods.handleSubmit(onSubmit, onValidationFailed)}
            className="flex flex-col flex-1 min-h-0"
          >
            <div className="space-y-4 overflow-y-auto flex-1 py-2 px-1">
              <FormField
                key="name"
                control={control}
                name="name"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Name</FormLabel>
                    {isTemplateSecret && (
                      <FormDescription className="text-sm">
                        This name is fixed by the selected template.
                      </FormDescription>
                    )}
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Name (snake case)"
                        readOnly={isTemplateSecret}
                        aria-readonly={isTemplateSecret}
                        {...register("name")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="description"
                control={control}
                name="description"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Description</FormLabel>
                    <FormDescription className="text-sm">
                      A description for this secret.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder="Description"
                        {...register("description")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                key="environment"
                control={control}
                name="environment"
                render={() => (
                  <FormItem>
                    <FormLabel className="text-sm">Environment</FormLabel>
                    <FormDescription className="text-sm">
                      The workflow&apos;s target execution environment.
                    </FormDescription>
                    <FormControl>
                      <Input
                        className="text-sm"
                        placeholder='Default environment: "default"'
                        {...register("environment")}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {!selectedTool && (
                <FormField
                  key="type"
                  control={control}
                  name="type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-sm">Type</FormLabel>
                      <FormDescription className="text-sm">
                        Choose how this secret is stored.
                      </FormDescription>
                      <Select
                        onValueChange={field.onChange}
                        value={field.value}
                      >
                        <FormControl>
                          <SelectTrigger className="text-sm">
                            <SelectValue placeholder="Select a type" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          <SelectItem value="custom">
                            <span className="flex items-center gap-2">
                              <Braces className="size-4" />
                              Key-value pair
                            </span>
                          </SelectItem>
                          <SelectItem value="ssh_key">
                            <span className="flex items-center gap-2">
                              <FileKey2 className="size-4" />
                              SSH private key
                            </span>
                          </SelectItem>
                          <SelectItem value="mtls">
                            <span className="flex items-center gap-2">
                              <KeyRoundIcon className="size-4" />
                              mTLS certificate + key
                            </span>
                          </SelectItem>
                          <SelectItem value="ca_cert">
                            <span className="flex items-center gap-2">
                              <ShieldCheck className="size-4" />
                              CA certificate
                            </span>
                          </SelectItem>
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {secretType === "custom" && isOktaCredentialForm && (
                <div className="space-y-4">
                  <FormField
                    key="okta_auth_method"
                    control={control}
                    name="okta_auth_method"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">Auth method</FormLabel>
                        <Select
                          onValueChange={(value) => {
                            const authMethod = value as OktaAuthMethod
                            field.onChange(authMethod)
                            methods.clearErrors([
                              "okta_api_token",
                              "okta_access_token",
                              "okta_service_token",
                              "okta_client_id",
                              "okta_scopes",
                              "okta_private_key",
                              "okta_dpop_key_rotation_interval",
                            ])
                          }}
                          value={field.value}
                        >
                          <FormControl>
                            <SelectTrigger className="text-sm">
                              <SelectValue placeholder="Select auth method" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {oktaAuthMethodOptions.map((option) => (
                              <SelectItem
                                key={option.value}
                                value={option.value}
                              >
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <FormField
                    key="okta_base_url"
                    control={control}
                    name="okta_base_url"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">Org URL</FormLabel>
                        <FormControl>
                          <Input
                            className="text-sm"
                            placeholder="https://dev-123456.okta.com"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription className="text-sm">
                          Optional when actions pass an org URL.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  {oktaAuthMethod === "ssws" && (
                    <FormField
                      key="okta_api_token"
                      control={control}
                      name="okta_api_token"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel className="text-sm">API token</FormLabel>
                          <FormControl>
                            <Input
                              className="text-sm"
                              placeholder="Okta SSWS API token"
                              type="password"
                              {...field}
                            />
                          </FormControl>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}

                  {oktaAuthMethod === "bearer" && (
                    <div className="grid gap-4 md:grid-cols-2">
                      <FormField
                        key="okta_access_token"
                        control={control}
                        name="okta_access_token"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Access token
                            </FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder="OAuth access token"
                                type="password"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                      <FormField
                        key="okta_service_token"
                        control={control}
                        name="okta_service_token"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Service token
                            </FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder="OAuth service token"
                                type="password"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}

                  {oktaAuthMethod === "private_key" && (
                    <div className="space-y-4">
                      <div className="grid gap-4 md:grid-cols-2">
                        <FormField
                          key="okta_client_id"
                          control={control}
                          name="okta_client_id"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel className="text-sm">
                                Client ID
                              </FormLabel>
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder="Okta OAuth client ID"
                                  {...field}
                                />
                              </FormControl>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                        <FormField
                          key="okta_scopes"
                          control={control}
                          name="okta_scopes"
                          render={({ field }) => (
                            <FormItem>
                              <FormLabel className="text-sm">Scopes</FormLabel>
                              <FormControl>
                                <Input
                                  className="text-sm"
                                  placeholder="okta.users.read okta.groups.read"
                                  {...field}
                                />
                              </FormControl>
                              <FormDescription className="text-sm">
                                Space or comma separated.
                              </FormDescription>
                              <FormMessage />
                            </FormItem>
                          )}
                        />
                      </div>

                      <FormField
                        key="okta_kid"
                        control={control}
                        name="okta_kid"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">Key ID</FormLabel>
                            <FormControl>
                              <Input
                                className="text-sm"
                                placeholder="Optional JWK key ID"
                                {...field}
                              />
                            </FormControl>
                            <FormMessage />
                          </FormItem>
                        )}
                      />

                      <FormField
                        key="okta_dpop_enabled"
                        control={control}
                        name="okta_dpop_enabled"
                        render={({ field }) => (
                          <FormItem className="space-y-3">
                            <div className="flex items-center justify-between gap-4">
                              <div className="space-y-1">
                                <FormLabel className="text-sm">DPoP</FormLabel>
                                <FormDescription className="text-sm">
                                  Enable proof-of-possession tokens.
                                </FormDescription>
                              </div>
                              <FormControl>
                                <Switch
                                  checked={field.value}
                                  onCheckedChange={(checked) => {
                                    field.onChange(checked)
                                    if (!checked) {
                                      methods.clearErrors(
                                        "okta_dpop_key_rotation_interval"
                                      )
                                    }
                                  }}
                                />
                              </FormControl>
                            </div>
                            {oktaDpopEnabled && (
                              <FormField
                                key="okta_dpop_key_rotation_interval"
                                control={control}
                                name="okta_dpop_key_rotation_interval"
                                render={({ field: intervalField }) => (
                                  <FormItem>
                                    <FormLabel className="text-sm">
                                      Key rotation interval
                                    </FormLabel>
                                    <FormControl>
                                      <Input
                                        className="text-sm"
                                        inputMode="numeric"
                                        placeholder="86400"
                                        {...intervalField}
                                      />
                                    </FormControl>
                                    <FormDescription className="text-sm">
                                      Optional seconds; SDK default is 86400.
                                    </FormDescription>
                                    <FormMessage />
                                  </FormItem>
                                )}
                              />
                            )}
                          </FormItem>
                        )}
                      />

                      <FormField
                        key="okta_private_key"
                        control={control}
                        name="okta_private_key"
                        render={({ field }) => (
                          <FormItem>
                            <FormLabel className="text-sm">
                              Private key
                            </FormLabel>
                            <input
                              ref={oktaPrivateKeyFileInputRef}
                              type="file"
                              accept=".pem,.json,application/json"
                              className="hidden"
                              onChange={handleOktaPrivateKeyInputChange}
                            />
                            <div
                              onDrop={handleOktaPrivateKeyDrop}
                              onDragEnter={handleOktaPrivateKeyDragOver}
                              onDragOver={handleOktaPrivateKeyDragOver}
                              onDragLeave={handleOktaPrivateKeyDragLeave}
                              className={cn(
                                "rounded-md transition-colors",
                                isOktaPrivateKeyDragOver &&
                                  "ring-1 ring-inset ring-ring"
                              )}
                            >
                              <FormControl>
                                <Textarea
                                  className="h-36 font-mono text-xs"
                                  placeholder="-----BEGIN PRIVATE KEY-----"
                                  {...field}
                                  onChange={(event) => {
                                    oktaPrivateKeyReadIdRef.current += 1
                                    setOktaPrivateKeyFileName(null)
                                    methods.clearErrors("okta_private_key")
                                    field.onChange(event)
                                  }}
                                />
                              </FormControl>
                            </div>
                            <div className="flex flex-wrap items-center justify-between gap-2">
                              <FormDescription className="text-sm">
                                Paste PEM or JWK JSON, or drop a .pem/.json file
                                into the field.
                              </FormDescription>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                onClick={handleOktaPrivateKeyFileChoose}
                              >
                                <UploadCloudIcon className="mr-2 size-4" />
                                {oktaPrivateKeyFileName
                                  ? "Replace file"
                                  : "Upload file"}
                              </Button>
                            </div>
                            {oktaPrivateKeyFileName && (
                              <p className="flex items-center gap-1 text-xs text-muted-foreground">
                                <FileTextIcon className="size-3.5" />
                                {oktaPrivateKeyFileName}
                              </p>
                            )}
                            <FormMessage />
                          </FormItem>
                        )}
                      />
                    </div>
                  )}
                </div>
              )}

              {secretType === "custom" && !isOktaCredentialForm && (
                <FormField
                  key={inputKey}
                  control={control}
                  name={typedKey}
                  render={() => (
                    <FormItem>
                      <FormLabel className="text-sm">Keys</FormLabel>
                      {isAwsAssumeRoleTemplate && (
                        <div className="space-y-3">
                          {awsAssumeRoleAccess ? (
                            <>
                              <Alert>
                                <InfoIcon className="size-4" />
                                <AlertTitle className="text-xs">
                                  Cross-role AWS access
                                </AlertTitle>
                                <AlertDescription className="space-y-3 text-xs">
                                  <p>
                                    Set <code>AWS_ROLE_ARN</code> to the role in
                                    the target AWS account. Tracecat assumes it
                                    with the principal and External ID below.{" "}
                                    <code>AWS_ROLE_SESSION_NAME</code> is not
                                    used and should not be added.
                                  </p>
                                  <dl className="grid gap-2 md:grid-cols-[140px_1fr]">
                                    <dt className="text-muted-foreground">
                                      Tracecat account
                                    </dt>
                                    <dd className="break-all font-mono text-foreground">
                                      {
                                        awsAssumeRoleAccess.tracecat_aws_account_id
                                      }
                                    </dd>
                                    <dt className="text-muted-foreground">
                                      Principal ARN
                                    </dt>
                                    <dd className="break-all font-mono text-foreground">
                                      {
                                        awsAssumeRoleAccess.tracecat_aws_principal_arn
                                      }
                                    </dd>
                                    <dt className="text-muted-foreground">
                                      External ID
                                    </dt>
                                    <dd className="break-all font-mono text-foreground">
                                      {awsAssumeRoleAccess.external_id}
                                    </dd>
                                    <dt className="text-muted-foreground">
                                      Target role
                                    </dt>
                                    <dd className="break-all font-mono text-foreground">
                                      {roleArn || "Paste AWS_ROLE_ARN below"}
                                    </dd>
                                  </dl>
                                </AlertDescription>
                              </Alert>
                              <div className="space-y-1">
                                <Label className="text-xs font-medium">
                                  Trust policy
                                </Label>
                                <JsonSyntaxBlock value={awsTrustPolicy} />
                              </div>
                              <p className="text-xs text-muted-foreground">
                                Create the role in the third-party account, add
                                the trust policy above, then paste that role ARN
                                into <code>AWS_ROLE_ARN</code>.
                              </p>
                            </>
                          ) : awsAssumeRoleAccessIsLoading ? (
                            <p className="text-xs text-muted-foreground">
                              Loading AWS role access details...
                            </p>
                          ) : shouldHideAwsAssumeRoleNote ? null : (
                            <p className="text-xs text-destructive">
                              {awsAssumeRoleAccessError?.message ||
                                "Unable to load AWS role access details."}
                            </p>
                          )}
                        </div>
                      )}
                      <div className="flex flex-col space-y-2">
                        {fields.map((field, index) => {
                          return (
                            <div
                              key={`${field.id}.${index}`}
                              className="flex w-full items-center gap-2"
                            >
                              <FormControl className="flex-1">
                                <Input
                                  id={`key-${index}`}
                                  className="text-sm"
                                  {...register(
                                    `${inputKey}.${index}.key` as const,
                                    {
                                      required: true,
                                    }
                                  )}
                                  placeholder="Key"
                                  disabled={
                                    !!selectedTool &&
                                    (selectedTool.keys?.includes(
                                      field.key || ""
                                    ) ||
                                      selectedTool.optional_keys?.includes(
                                        field.key || ""
                                      ))
                                  }
                                />
                              </FormControl>
                              <FormControl className="flex-1">
                                <Input
                                  id={`value-${index}`}
                                  className="text-sm"
                                  {...register(
                                    `${inputKey}.${index}.value` as const,
                                    {
                                      required: !field.isOptional,
                                    }
                                  )}
                                  placeholder={
                                    field.isOptional
                                      ? "Value (optional)"
                                      : "Value"
                                  }
                                  type="password"
                                />
                              </FormControl>

                              <Button
                                type="button"
                                variant="ghost"
                                onClick={() => remove(index)}
                                disabled={
                                  (!!selectedTool &&
                                    (selectedTool.keys?.includes(
                                      field.key || ""
                                    ) ||
                                      selectedTool.optional_keys?.includes(
                                        field.key || ""
                                      ))) ||
                                  (!selectedTool && fields.length === 1)
                                }
                              >
                                <Trash2Icon className="size-3.5" />
                              </Button>
                            </div>
                          )
                        })}
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => append({ key: "", value: "" })}
                          className="space-x-2 text-xs"
                        >
                          <PlusCircle className="mr-2 size-4" />
                          Add Item
                        </Button>
                      </div>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}
              {secretType === "ssh_key" && (
                <SshPrivateKeyField
                  control={control}
                  register={register}
                  name="private_key"
                />
              )}
              {secretType === "mtls" && (
                <>
                  {renderTextareaField(
                    "tls_certificate",
                    "TLS certificate",
                    "-----BEGIN CERTIFICATE-----"
                  )}
                  {renderTextareaField(
                    "tls_private_key",
                    "TLS private key",
                    "-----BEGIN PRIVATE KEY-----"
                  )}
                </>
              )}
              {secretType === "ca_cert" &&
                renderTextareaField(
                  "ca_certificate",
                  "CA certificate",
                  "-----BEGIN CERTIFICATE-----"
                )}
            </div>
            <DialogFooter className="flex-shrink-0 pt-4">
              <Button className="ml-auto space-x-2" type="submit">
                <KeyRoundIcon className="mr-2 size-4" />
                Create secret
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

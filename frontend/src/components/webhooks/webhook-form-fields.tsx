"use client"

import { CheckIcon } from "@radix-ui/react-icons"
import * as ipaddr from "ipaddr.js"
import { z } from "zod"
import { $WebhookMethod, $WebhookStatus, type WebhookMethod } from "@/client"
import { CustomTagInput, type Tag } from "@/components/tags-input"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { cn } from "@/lib/utils"

/**
 * The webhook settings a workflow's webhook panel and the Triggers tab both
 * edit: allowed methods, the IP allowlist and whether request headers reach
 * the workflow. The panel saves each change as it happens; the Triggers tab
 * collects them into a new trigger's config.
 */

/** Every HTTP method a webhook can accept. */
export const HTTP_METHODS: readonly WebhookMethod[] = $WebhookMethod.enum

function toCanonicalString(address: ipaddr.IPv4 | ipaddr.IPv6): string {
  const maybeNormalized =
    (address as ipaddr.IPv6).toNormalizedString?.() ?? address.toString()
  return maybeNormalized
}

/** One allowlist entry as an IPv4 CIDR, or why it isn't one. */
export function validateAndNormalizeCidr(
  value: string
): { normalized: string } | { error: string } {
  try {
    const [address, prefixLength] = ipaddr.parseCIDR(value)
    if (address.kind() !== "ipv4") {
      throw new Error("Only IPv4 CIDR ranges are supported")
    }
    const normalized = `${toCanonicalString(address)}/${prefixLength}`
    return { normalized }
  } catch {
    try {
      const address = ipaddr.parse(value)
      if (address.kind() !== "ipv4") {
        throw new Error("Only IPv4 addresses are supported")
      }
      const prefixLength = 32
      const normalized = `${toCanonicalString(address)}/${prefixLength}`
      return { normalized }
    } catch {
      return {
        error: `Invalid IPv4 address or CIDR: "${value}"`,
      }
    }
  }
}

/**
 * The allowlist entries as canonical, de-duplicated CIDR tags, or the first
 * entry that isn't valid.
 */
export function normalizeCidrTags(
  tags: Tag[]
): { tags: Tag[] } | { error: string } {
  const normalized = new Map<string, Tag>()
  for (const tag of tags) {
    const trimmed = tag.text.trim()
    if (!trimmed) {
      return { error: "Invalid IP address or CIDR" }
    }
    const result = validateAndNormalizeCidr(trimmed)
    if ("error" in result) {
      return { error: result.error }
    }
    const canonical = result.normalized
    if (!normalized.has(canonical)) {
      normalized.set(canonical, { id: canonical, text: canonical })
    }
  }
  return { tags: Array.from(normalized.values()) }
}

/** The workflow webhook panel's form values. */
export const webhookFormSchema = z.object({
  status: z.enum($WebhookStatus.enum),
  methods: z
    .array(z.enum($WebhookMethod.enum))
    .min(1, "At least one method is required"),
  allowlisted_cidrs: z
    .array(
      z.object({
        id: z.string(),
        text: z.string().trim().min(1, "Invalid IP address or CIDR"),
      })
    )
    .superRefine((cidrs, ctx) => {
      for (const cidr of cidrs) {
        const result = validateAndNormalizeCidr(cidr.text)
        if ("error" in result) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: result.error,
          })
          return
        }
      }
    })
    .transform((cidrs) => {
      const normalized = new Map<string, Tag>()
      for (const cidr of cidrs) {
        const result = validateAndNormalizeCidr(cidr.text)
        if ("error" in result) {
          continue
        }
        const value = result.normalized
        if (!normalized.has(value)) {
          normalized.set(value, { id: value, text: value })
        }
      }
      return Array.from(normalized.values())
    })
    .default([]),
  include_headers: z.boolean().default(false),
})

export type WebhookForm = z.infer<typeof webhookFormSchema>

/** Turns passing request headers and metadata to the trigger on or off. */
export function WebhookIncludeHeadersToggle({
  checked,
  onCheckedChange,
  disabled,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
}) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border p-3">
      <div className="space-y-1">
        <Label className="text-xs font-semibold">Include request headers</Label>
        <p className="text-xs text-muted-foreground">
          Pass request headers and metadata to TRIGGER as{" "}
          <code>{"{ status_code, headers, data }"}</code> instead of just the
          body
        </p>
      </div>
      <Switch
        aria-label="Include request headers"
        checked={checked}
        onCheckedChange={onCheckedChange}
        className="data-[state=checked]:bg-emerald-500"
        disabled={disabled}
      />
    </div>
  )
}

/** Picks the HTTP methods a webhook accepts. Hands back the toggled list. */
export function WebhookMethodsSelect({
  value,
  onChange,
  disabled,
}: {
  value: WebhookMethod[]
  onChange: (methods: WebhookMethod[]) => void
  disabled?: boolean
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="justify-between w-full text-xs"
          disabled={disabled}
          aria-label="Allowed HTTP methods"
        >
          {value.length > 0
            ? [...value].sort().join(", ")
            : "Select HTTP methods"}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        style={{
          width: "var(--radix-dropdown-menu-trigger-width)",
        }}
        align="start"
        sideOffset={4}
      >
        {HTTP_METHODS.map((method) => (
          <DropdownMenuItem
            key={method}
            onClick={() => {
              const next = value.includes(method)
                ? value.filter((m) => m !== method)
                : [...value, method]
              onChange(next)
            }}
            className="w-full text-xs"
          >
            <CheckIcon
              className={cn(
                "mr-2 size-4",
                value.includes(method) ? "opacity-100" : "opacity-0"
              )}
            />
            <span>{method}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** The IP allowlist entries. Hands back the raw tags; callers normalize. */
export function WebhookAllowlistInput({
  tags,
  onTagsChange,
  disabled,
}: {
  tags: Tag[]
  onTagsChange: (tags: Tag[]) => void
  disabled?: boolean
}) {
  return (
    <CustomTagInput
      placeholder="Enter an IP address or CIDR. Allow all IPs by default."
      tags={tags}
      disabled={disabled}
      setTags={(next) => {
        const resolved = typeof next === "function" ? next(tags) : next
        onTagsChange(Array.isArray(resolved) ? resolved : [])
      }}
    />
  )
}

/** "Press Enter to add a new entry", under the allowlist input. */
export function WebhookAllowlistHint() {
  return (
    <>
      Press{" "}
      <kbd className="px-1 py-0.5 text-[10px] font-semibold bg-muted border rounded tracking-tighter">
        Enter ↵
      </kbd>{" "}
      to add a new entry.
    </>
  )
}

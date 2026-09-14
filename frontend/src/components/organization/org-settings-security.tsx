"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  CheckCircle2Icon,
  PlusIcon,
  ShieldAlertIcon,
  Trash2Icon,
  XCircleIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { IPAllowlistCheckResult } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
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
import { Switch } from "@/components/ui/switch"
import { useOrgSecuritySettings } from "@/hooks/use-org-security-settings"

const IPV4_CIDR_PATTERN =
  /^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}(\/(3[0-2]|[12]?\d))?$/
const IPV6_CIDR_PATTERN = /^[0-9a-fA-F:]+(\/(12[0-8]|1[01]\d|[1-9]?\d))?$/

function isValidCidr(value: string): boolean {
  const trimmed = value.trim()
  if (IPV4_CIDR_PATTERN.test(trimmed)) {
    return true
  }
  return trimmed.includes(":") && IPV6_CIDR_PATTERN.test(trimmed)
}

const securityFormSchema = z.object({
  ip_allowlist_enabled: z.boolean(),
  cidrs: z.array(
    z.object({
      value: z
        .string()
        .trim()
        .refine((v) => v === "" || isValidCidr(v), {
          message: "Enter a valid IPv4/IPv6 address or CIDR range",
        }),
    })
  ),
})

type SecurityFormValues = z.infer<typeof securityFormSchema>

/**
 * Organization IP allowlist settings form.
 */
export function OrgSettingsSecurityForm() {
  const {
    securitySettings,
    securitySettingsIsLoading,
    securitySettingsError,
    updateSecuritySettings,
    updateSecuritySettingsIsPending,
    checkIpAllowlist,
    checkIpAllowlistIsPending,
  } = useOrgSecuritySettings()

  const form = useForm<SecurityFormValues>({
    resolver: zodResolver(securityFormSchema),
    defaultValues: { ip_allowlist_enabled: false, cidrs: [] },
  })
  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "cidrs",
  })

  useEffect(() => {
    if (!securitySettings) {
      return
    }
    form.reset({
      ip_allowlist_enabled: securitySettings.ip_allowlist_enabled,
      cidrs: securitySettings.ip_allowlist_cidrs.map((value) => ({ value })),
    })
  }, [securitySettings, form])

  const [checkIp, setCheckIp] = useState("")
  const [checkResult, setCheckResult] = useState<IPAllowlistCheckResult | null>(
    null
  )

  if (securitySettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (securitySettingsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading security settings: ${securitySettingsError.message}`}
      />
    )
  }

  const enabled = form.watch("ip_allowlist_enabled")
  const nonEmptyCidrs = form
    .watch("cidrs")
    .map((c) => c.value.trim())
    .filter((v) => v !== "")

  async function onSubmit(values: SecurityFormValues) {
    await updateSecuritySettings({
      ip_allowlist_enabled: values.ip_allowlist_enabled,
      ip_allowlist_cidrs: values.cidrs
        .map((c) => c.value.trim())
        .filter((v) => v !== ""),
    })
  }

  async function onCheckIp() {
    if (!checkIp.trim()) {
      return
    }
    try {
      setCheckResult(await checkIpAllowlist({ ip_address: checkIp.trim() }))
    } catch {
      setCheckResult(null)
    }
  }

  return (
    <div className="space-y-12">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
          <FormField
            control={form.control}
            name="ip_allowlist_enabled"
            render={({ field }) => (
              <FormItem className="flex items-center justify-between rounded-lg border p-4">
                <div className="space-y-0.5">
                  <FormLabel>Enforce IP allowlist</FormLabel>
                  <FormDescription>
                    Only allow sign-in and API access to this organization from
                    the IP addresses below. Platform superusers are exempt.
                  </FormDescription>
                </div>
                <FormControl>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
              </FormItem>
            )}
          />

          {enabled && nonEmptyCidrs.length === 0 && (
            <Alert>
              <ShieldAlertIcon className="size-4" />
              <AlertTitle>No IP ranges configured</AlertTitle>
              <AlertDescription>
                The allowlist is not enforced until at least one IP address or
                CIDR range is added.
              </AlertDescription>
            </Alert>
          )}

          <div className="space-y-3">
            <div>
              <FormLabel>Allowed IP addresses and CIDR ranges</FormLabel>
              <p className="text-sm text-muted-foreground">
                IPv4 or IPv6, for example 203.0.113.0/24 or 2001:db8::/32. Your
                current IP must be included before enforcement can be enabled.
              </p>
            </div>
            {fields.map((item, index) => (
              <FormField
                key={item.id}
                control={form.control}
                name={`cidrs.${index}.value`}
                render={({ field }) => (
                  <FormItem>
                    <div className="flex items-center gap-2">
                      <FormControl>
                        <Input
                          {...field}
                          placeholder="203.0.113.0/24"
                          className="font-mono"
                          autoComplete="off"
                          spellCheck={false}
                        />
                      </FormControl>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label="Remove IP range"
                        onClick={() => remove(index)}
                      >
                        <Trash2Icon className="size-4" />
                      </Button>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />
            ))}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => append({ value: "" })}
            >
              <PlusIcon className="mr-2 size-4" />
              Add IP range
            </Button>
          </div>

          <Button type="submit" disabled={updateSecuritySettingsIsPending}>
            Save
          </Button>
        </form>
      </Form>

      <div className="space-y-3">
        <div>
          <h3 className="text-lg font-semibold tracking-tight">
            Validate an IP address
          </h3>
          <p className="text-sm text-muted-foreground">
            Check whether an IP address is admitted by the saved allowlist.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Input
            value={checkIp}
            onChange={(e) => {
              setCheckIp(e.target.value)
              setCheckResult(null)
            }}
            placeholder="203.0.113.7"
            className="max-w-sm font-mono"
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            type="button"
            variant="outline"
            onClick={onCheckIp}
            disabled={checkIpAllowlistIsPending || !checkIp.trim()}
          >
            Validate
          </Button>
        </div>
        {checkResult && <IpCheckResult result={checkResult} />}
      </div>
    </div>
  )
}

function IpCheckResult({ result }: { result: IPAllowlistCheckResult }) {
  if (!result.enforced) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <CheckCircle2Icon className="size-4" />
        Allowed. The allowlist is not currently enforced.
      </p>
    )
  }
  if (result.allowed) {
    return (
      <p className="flex items-center gap-2 text-sm text-emerald-700">
        <CheckCircle2Icon className="size-4" />
        Allowed by {result.matched_cidr}
      </p>
    )
  }
  return (
    <p className="flex items-center gap-2 text-sm text-destructive">
      <XCircleIcon className="size-4" />
      Not allowed by the current allowlist
    </p>
  )
}

"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { AlertTriangleIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { SAMLSettingsUpdate } from "@/client"
import { CopyButton } from "@/components/copy-button"
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
import { TooltipProvider } from "@/components/ui/tooltip"
import { useOrgDomains } from "@/hooks/use-org-domains"
import { useAppInfo, useOrgSamlSettings } from "@/lib/hooks"

const ssoFormSchema = z.object({
  saml_enabled: z.boolean(),
  saml_enforced: z.boolean(),
  saml_idp_metadata_url: z.string().url().nullish(),
  saml_sp_acs_url: z.string().url().nullish(),
})

type SsoFormValues = z.infer<typeof ssoFormSchema>

export function OrgSettingsSsoForm() {
  const { appInfo } = useAppInfo()
  const { domains } = useOrgDomains()
  const {
    samlSettings,
    samlSettingsIsLoading,
    samlSettingsError,
    updateSamlSettings,
    updateSamlSettingsIsPending,
  } = useOrgSamlSettings()
  const methods = useForm<SsoFormValues>({
    resolver: zodResolver(ssoFormSchema),
    values: {
      saml_enabled: samlSettings?.saml_enabled ?? false,
      saml_enforced: samlSettings?.saml_enforced ?? false,
      saml_idp_metadata_url: samlSettings?.saml_idp_metadata_url,
      saml_sp_acs_url: samlSettings?.saml_sp_acs_url,
    },
  })

  const isSamlAllowed = appInfo?.auth_allowed_types.includes("saml")
  const requiresDomainGuard = appInfo?.ee_multi_tenant ?? false
  const hasActiveDomains = Boolean(domains?.some((domain) => domain.is_active))
  const samlEnabled = methods.watch("saml_enabled")

  const canEnableSaml = !requiresDomainGuard || hasActiveDomains
  const onSubmit = async (data: SsoFormValues) => {
    if (requiresDomainGuard && !hasActiveDomains && data.saml_enabled) {
      console.error(
        "Refusing to enable SAML without at least one active org domain in multi-tenant mode"
      )
      return
    }
    const conditional: Partial<SAMLSettingsUpdate> = {}
    if (isSamlAllowed) {
      conditional.saml_enabled = data.saml_enabled
      conditional.saml_enforced = data.saml_enabled && data.saml_enforced
    }
    try {
      await updateSamlSettings({
        requestBody: {
          saml_idp_metadata_url: data.saml_idp_metadata_url,
          ...conditional,
        },
      })
    } catch (error) {
      console.error("Failed to update SAML settings", error)
    }
  }

  if (samlSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (samlSettingsError || !samlSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading SAML settings: ${samlSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  const failedKeys = samlSettings.decryption_failed_keys ?? []

  return (
    <Form {...methods}>
      <form onSubmit={methods.handleSubmit(onSubmit)} className="space-y-8">
        {failedKeys.length > 0 && (
          <Alert>
            <AlertTriangleIcon className="size-4 !text-destructive" />
            <AlertTitle className="text-destructive">
              Unable to decrypt organization settings
            </AlertTitle>
            <AlertDescription>
              Some encrypted values could not be decrypted for{" "}
              {failedKeys.join(", ")}. Please reconfigure and save these
              settings again.
            </AlertDescription>
          </Alert>
        )}
        {requiresDomainGuard && !hasActiveDomains && (
          <Alert>
            <AlertTriangleIcon className="size-4 !text-destructive" />
            <AlertTitle className="text-destructive">
              Domain guardrail required
            </AlertTitle>
            <AlertDescription>
              Domain allowlist must be configured to enable SAML SSO. Contact a
              Tracecat platform admin to set this up for your organization.
            </AlertDescription>
          </Alert>
        )}
        <FormField
          control={methods.control}
          name="saml_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable SAML SSO</FormLabel>
                <FormDescription>
                  Enable SAML SSO for your organization.
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  disabled={!canEnableSaml && !field.value}
                />
              </FormControl>
            </FormItem>
          )}
        />
        <FormField
          control={methods.control}
          name="saml_enforced"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enforce SAML SSO</FormLabel>
                <FormDescription>
                  When enabled, users in this organization must authenticate via
                  SAML. Password login will be disabled for matching email
                  domains.
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  disabled={(!canEnableSaml && !field.value) || !samlEnabled}
                />
              </FormControl>
            </FormItem>
          )}
        />
        <FormField
          control={methods.control}
          name="saml_idp_metadata_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>IdP metadata URL</FormLabel>
              <FormControl>
                <Input
                  placeholder="https://tenant.provider.com/app/hash/sso/saml/metadata"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormDescription>
                This is the Metadata URL from your IdP.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        {/* NOTE: This is read only, set by the platform */}
        <FormField
          control={methods.control}
          name="saml_sp_acs_url"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel className="flex items-center gap-2">
                <span>Service provider ACS URL</span>
                <TooltipProvider>
                  {field.value && (
                    <CopyButton
                      value={field.value}
                      toastMessage="Copied Service provider ACS URL to clipboard"
                    />
                  )}
                </TooltipProvider>
              </FormLabel>
              <FormControl>
                <Input
                  placeholder="http://localhost/auth/saml/acs"
                  {...field}
                  value={field.value ?? ""}
                  disabled
                />
              </FormControl>
              <FormDescription>
                This is the endpoint where the Service Provider receives and
                processes SAML assertions from the IdP after successful
                authentication.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" disabled={updateSamlSettingsIsPending}>
          Update SSO settings
        </Button>
      </form>
    </Form>
  )
}

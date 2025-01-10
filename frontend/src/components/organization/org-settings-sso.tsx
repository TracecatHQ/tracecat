"use client"

import { SAMLSettingsUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useAppInfo, useOrgSamlSettings } from "@/lib/hooks"
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
import { CopyButton } from "@/components/copy-button"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

const ssoFormSchema = z.object({
  saml_enabled: z.boolean(),
  saml_idp_metadata_url: z.string().url().nullish(),
  saml_sp_acs_url: z.string().url().nullish(),
})

type SsoFormValues = z.infer<typeof ssoFormSchema>

export function OrgSettingsSsoForm() {
  const { appInfo } = useAppInfo()
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
      saml_idp_metadata_url: samlSettings?.saml_idp_metadata_url,
      saml_sp_acs_url: samlSettings?.saml_sp_acs_url,
    },
  })

  const isSamlAllowed = appInfo?.auth_allowed_types.includes("saml")
  const onSubmit = async (data: SsoFormValues) => {
    const conditional: Partial<SAMLSettingsUpdate> = {}
    if (isSamlAllowed) {
      conditional.saml_enabled = data.saml_enabled
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

  return (
    <Form {...methods}>
      <form onSubmit={methods.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={methods.control}
          name="saml_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel className="text-base"> Enable SAML SSO</FormLabel>
                <FormDescription>
                  Enable SAML SSO for your organization.
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
        <FormField
          control={methods.control}
          name="saml_idp_metadata_url"
          render={({ field }) => (
            <FormItem>
              <FormLabel>IdP Metadata URL</FormLabel>
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
                <span>Service Provider ACS URL</span>
                <TooltipProvider>
                  {field.value && (
                    <CopyButton
                      value={field.value}
                      toastMessage="Copied Service Provider ACS URL to clipboard"
                    />
                  )}
                </TooltipProvider>
              </FormLabel>
              <FormControl>
                <Input
                  placeholder="http://localhost/api/auth/saml/acs"
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

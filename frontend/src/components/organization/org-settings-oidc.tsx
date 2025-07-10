"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { OAuthSettingsUpdate } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { Switch } from "@/components/ui/switch"
import { useAppInfo, useOrgOAuthSettings } from "@/lib/hooks"

const oidcFormSchema = z.object({
  oidc_enabled: z.boolean(),
})

type OidcFormValues = z.infer<typeof oidcFormSchema>

export function OrgSettingsOidcForm() {
  const { appInfo } = useAppInfo()
  const {
    oauthSettings,
    oauthSettingsIsLoading,
    oauthSettingsError,
    updateOAuthSettings,
    updateOAuthSettingsIsPending,
  } = useOrgOAuthSettings()

  const form = useForm<OidcFormValues>({
    resolver: zodResolver(oidcFormSchema),
    values: {
      oidc_enabled: oauthSettings?.oidc_enabled ?? false,
    },
  })

  const isOidcAllowed = appInfo?.auth_allowed_types.includes("oidc")
  const onSubmit = async (data: OidcFormValues) => {
    const conditional: Partial<OAuthSettingsUpdate> = {}
    if (isOidcAllowed) {
      conditional.oidc_enabled = data.oidc_enabled
    }
    try {
      await updateOAuthSettings({
        requestBody: conditional,
      })
    } catch {
      console.error("Failed to update OIDC settings")
    }
  }

  if (oauthSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (oauthSettingsError || !oauthSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading OIDC settings: ${oauthSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="oidc_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable OpenID Connect sign-in</FormLabel>
                <FormDescription>
                  {isOidcAllowed
                    ? "Allow members to sign in to your organization using an OpenID Connect provider."
                    : "OpenID Connect sign-in is not available in this deployment. Contact your system administrator to enable this feature."}
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={isOidcAllowed && field.value}
                  onCheckedChange={field.onChange}
                  disabled={!isOidcAllowed}
                  aria-disabled={!isOidcAllowed}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Button type="submit" disabled={!isOidcAllowed || updateOAuthSettingsIsPending}>
          Update OIDC settings
        </Button>
      </form>
    </Form>
  )
}

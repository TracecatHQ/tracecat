"use client"

import { OAuthSettingsUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useAppInfo, useOrgOAuthSettings } from "@/lib/hooks"
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
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

const authFormSchema = z.object({
  oauth_google_enabled: z.boolean(),
})

type AuthFormValues = z.infer<typeof authFormSchema>

export function OrgSettingsOAuthForm() {
  const { appInfo } = useAppInfo()
  const {
    oauthSettings,
    oauthSettingsIsLoading,
    oauthSettingsError,
    updateOAuthSettings,
    updateOAuthSettingsIsPending,
  } = useOrgOAuthSettings()

  const form = useForm<AuthFormValues>({
    resolver: zodResolver(authFormSchema),
    values: {
      oauth_google_enabled: oauthSettings?.oauth_google_enabled ?? false,
    },
  })

  const isOauthAllowed = appInfo?.auth_allowed_types.includes("google_oauth")
  const onSubmit = async (data: AuthFormValues) => {
    const conditional: Partial<OAuthSettingsUpdate> = {}
    if (isOauthAllowed) {
      conditional.oauth_google_enabled = data.oauth_google_enabled
    }
    try {
      await updateOAuthSettings({
        requestBody: conditional,
      })
    } catch {
      console.error("Failed to update auth settings")
    }
  }

  if (oauthSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (oauthSettingsError || !oauthSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading OAuth settings: ${oauthSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="oauth_google_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel>Enable Google OAuth sign-in</FormLabel>
                <FormDescription>
                  {isOauthAllowed
                    ? "Allow members to sign in to your organization using their Google accounts."
                    : "Google OAuth sign-in is not available in this deployment. Contact your system administrator to enable this feature."}
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={isOauthAllowed && field.value}
                  onCheckedChange={field.onChange}
                  disabled={!isOauthAllowed}
                  aria-disabled={!isOauthAllowed}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <Button
          type="submit"
          disabled={!isOauthAllowed || updateOAuthSettingsIsPending}
        >
          Update OAuth settings
        </Button>
      </form>
    </Form>
  )
}

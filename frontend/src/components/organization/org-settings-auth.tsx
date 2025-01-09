"use client"

import { AuthSettingsUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useAppInfo, useOrgAuthSettings } from "@/lib/hooks"
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
import { Switch } from "@/components/ui/switch"
import { CenteredSpinner } from "@/components/loading/spinner"
import { CustomTagInput } from "@/components/tags-input"

const authFormSchema = z.object({
  auth_basic_enabled: z.boolean(),
  auth_allowed_email_domains: z.array(
    z.object({
      id: z.string(),
      text: z.string().min(1, "Cannot be empty"),
    })
  ),
  auth_require_email_verification: z.boolean(),
  auth_min_password_length: z.number().min(1, "Must be greater than 0"),
  auth_session_expire_time_seconds: z.number().min(1, "Must be greater than 0"),
})

type AuthFormValues = z.infer<typeof authFormSchema>

export function OrgSettingsAuthForm() {
  const { appInfo } = useAppInfo()
  const {
    authSettings,
    authSettingsIsLoading,
    updateAuthSettings,
    updateAuthSettingsIsPending,
  } = useOrgAuthSettings()

  const form = useForm<AuthFormValues>({
    resolver: zodResolver(authFormSchema),
    values: {
      auth_basic_enabled: authSettings?.auth_basic_enabled ?? true,
      auth_allowed_email_domains:
        authSettings?.auth_allowed_email_domains?.map((domain) => ({
          id: domain,
          text: domain,
        })) ?? [],
      auth_require_email_verification:
        authSettings?.auth_require_email_verification ?? false,
      auth_min_password_length: authSettings?.auth_min_password_length ?? 12,
      auth_session_expire_time_seconds:
        authSettings?.auth_session_expire_time_seconds ?? 3600,
    },
  })
  const isBasicAuthAllowed = appInfo?.auth_allowed_types.includes("basic")
  const onSubmit = async (data: AuthFormValues) => {
    const conditional: Partial<AuthSettingsUpdate> = {}
    if (isBasicAuthAllowed) {
      conditional.auth_basic_enabled = data.auth_basic_enabled
    }
    try {
      await updateAuthSettings({
        requestBody: {
          auth_allowed_email_domains: data.auth_allowed_email_domains.map(
            (provider) => provider.text
          ),
          auth_require_email_verification: data.auth_require_email_verification,
          auth_min_password_length: data.auth_min_password_length,
          auth_session_expire_time_seconds:
            data.auth_session_expire_time_seconds,
          ...conditional,
        },
      })
    } catch {
      console.error("Failed to update Auth settings")
    }
  }

  if (authSettingsIsLoading) {
    return <CenteredSpinner />
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="auth_basic_enabled"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel className="text-base">
                  Enable Email/Password Authentication
                </FormLabel>
                <FormDescription>
                  {isBasicAuthAllowed
                    ? "Allow users to sign in with email and password. When disabled, only SSO/Oauth login methods will be available."
                    : "Email/password authentication is not available in this deployment. Contact your system administrator to enable this feature."}
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={isBasicAuthAllowed && field.value}
                  onCheckedChange={field.onChange}
                  disabled={!isBasicAuthAllowed}
                  aria-disabled={!isBasicAuthAllowed}
                />
              </FormControl>
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="auth_require_email_verification"
          render={({ field }) => (
            <FormItem className="flex flex-row items-center justify-between rounded-lg border p-4">
              <div className="space-y-0.5">
                <FormLabel className="text-base">
                  Require Email Verification
                </FormLabel>
                <FormDescription>
                  Require email verification for your organization.
                </FormDescription>
              </div>
              <FormControl>
                <Switch
                  checked={field.value}
                  onCheckedChange={field.onChange}
                  disabled
                />
              </FormControl>
            </FormItem>
          )}
        />

        <FormField
          control={form.control}
          name="auth_allowed_email_domains"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Allowed Sign-up Email Domains</FormLabel>
              <FormControl>
                <CustomTagInput
                  {...field}
                  placeholder="Enter a domain..."
                  tags={field.value}
                  setTags={field.onChange}
                />
              </FormControl>
              <FormDescription>
                Add domains that are allowed to sign up to the platform. (e.g.,
                acme.com)
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />

        <Button type="submit" disabled={updateAuthSettingsIsPending}>
          Update Auth Settings
        </Button>
      </form>
    </Form>
  )
}

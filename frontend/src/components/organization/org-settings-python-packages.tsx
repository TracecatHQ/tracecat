"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
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
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { useOrgPythonPackageSettings } from "@/hooks/use-org-python-package-settings"

const pythonPackagesFormSchema = z.object({
  python_package_index_url: z
    .string()
    .trim()
    .max(2048)
    .refine((url) => url === "" || /^https:\/\/[^/@?#\s]+/.test(url), {
      message: "Must be an https URL without credentials",
    }),
  python_package_index_username: z.string().trim().max(256),
  python_package_index_password: z.string().max(4096),
})

type PythonPackagesFormValues = z.infer<typeof pythonPackagesFormSchema>

/** Organization package index used to install run_python dependencies. */
export function OrgSettingsPythonPackagesForm() {
  const {
    pythonPackageSettings,
    pythonPackageSettingsIsLoading,
    pythonPackageSettingsError,
    updatePythonPackageSettings,
    updatePythonPackageSettingsIsPending,
  } = useOrgPythonPackageSettings()

  const form = useForm<PythonPackagesFormValues>({
    resolver: zodResolver(pythonPackagesFormSchema),
    values: {
      python_package_index_url:
        pythonPackageSettings?.python_package_index_url ?? "",
      python_package_index_username:
        pythonPackageSettings?.python_package_index_username ?? "",
      python_package_index_password: "",
    },
    mode: "onChange",
  })
  const passwordConfigured =
    pythonPackageSettings?.python_package_index_password_configured === true

  const onSubmit = async (data: PythonPackagesFormValues) => {
    try {
      await updatePythonPackageSettings({
        requestBody: {
          python_package_index_url: data.python_package_index_url || null,
          python_package_index_username:
            data.python_package_index_username || null,
          // Blank keeps the saved password; the API never returns it.
          ...(data.python_package_index_password
            ? {
                python_package_index_password:
                  data.python_package_index_password,
              }
            : {}),
        },
      })
    } catch {
      console.error("Failed to update Python package settings")
    }
  }

  const onClearPassword = async () => {
    try {
      await updatePythonPackageSettings({
        requestBody: { python_package_index_password: null },
      })
    } catch {
      console.error("Failed to clear Python package index password")
    }
  }

  if (pythonPackageSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (pythonPackageSettingsError || !pythonPackageSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading Python package settings: ${pythonPackageSettingsError?.message || "Unknown error"}`}
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="python_package_index_url"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel>Package index URL</FormLabel>
              <FormControl>
                <Input
                  placeholder="https://example.jfrog.io/artifactory/api/pypi/pypi-virtual/simple"
                  {...field}
                />
              </FormControl>
              <FormDescription>
                A PEP 503 simple index that replaces PyPI for Python script
                dependencies. Point it at a virtual repository that also proxies
                PyPI so public packages keep resolving. Must be https and
                publicly reachable. Leave blank to use the default index.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="python_package_index_username"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel>Username</FormLabel>
              <FormControl>
                <Input autoComplete="off" {...field} />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="python_package_index_password"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel>Password or access token</FormLabel>
              <div className="flex gap-2">
                <FormControl>
                  <Input
                    className="flex-1"
                    type="password"
                    autoComplete="new-password"
                    placeholder={
                      passwordConfigured
                        ? "Saved. Enter a new value to replace it."
                        : ""
                    }
                    {...field}
                  />
                </FormControl>
                {passwordConfigured && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={onClearPassword}
                    disabled={updatePythonPackageSettingsIsPending}
                  >
                    Clear
                  </Button>
                )}
              </div>
              <FormDescription>
                Encrypted at rest and never shown again. Use a read-only token
                scoped to this repository. When a password is set, only prebuilt
                wheels are installed and source builds are disabled. Changing
                the URL clears the saved password.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <Button type="submit" disabled={updatePythonPackageSettingsIsPending}>
          Save Python package settings
        </Button>
      </form>
    </Form>
  )
}

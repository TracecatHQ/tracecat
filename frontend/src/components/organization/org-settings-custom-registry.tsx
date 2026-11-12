"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { OrgRegistryRepositoryRemoveSection } from "@/components/organization/org-registry-repository-remove"
import { OrgRegistrySshKeySection } from "@/components/organization/org-registry-ssh-key"
import { CustomTagInput } from "@/components/tags-input"
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
import { validateGitSshUrl } from "@/lib/git"
import { useOrgGitSettings } from "@/lib/hooks"

export const gitFormSchema = z.object({
  git_allowed_domains: z.array(
    z.object({
      id: z.string(),
      text: z.string().min(1, "Cannot be empty"),
    })
  ),
  git_repo_url: z
    .string()
    .nullish()
    // Empty string signals removal
    .transform((url) => url?.trim() || null)
    .superRefine((url, ctx) => validateGitSshUrl(url, ctx)),
  git_repo_package_name: z
    .string()
    .nullish()
    // Empty string signals removal
    .transform((name) => name?.trim() || null),
})

type GitFormValues = z.infer<typeof gitFormSchema>

function toRegistryOrigin(url: string): string {
  return url.trim().split(/[?#]/)[0]
}

/** Whether saving `nextUrl` replaces, and so deletes, the repository of `previousUrl`. */
export function isRepositoryUrlReplaced(
  previousUrl: string | null | undefined,
  nextUrl: string | null | undefined
): boolean {
  if (!previousUrl?.trim() || !nextUrl?.trim()) {
    return false
  }
  return toRegistryOrigin(previousUrl) !== toRegistryOrigin(nextUrl)
}

/**
 * Lets the SSH key section sit between the form fields and the submit
 * button without nesting its dialogs inside the Git settings form.
 */
const GIT_SETTINGS_FORM_ID = "org-git-settings-form"

/** Git repository settings for the custom registry, plus the SSH key used to clone it. */
export function OrgSettingsCustomRegistryForm() {
  const {
    gitSettings,
    gitSettingsIsLoading,
    gitSettingsError,
    updateGitSettings,
    updateGitSettingsIsPending,
  } = useOrgGitSettings()

  const form = useForm<GitFormValues>({
    resolver: zodResolver(gitFormSchema),
    values: {
      git_allowed_domains: gitSettings?.git_allowed_domains?.map(
        (domain, index) => ({
          id: index.toString(),
          text: domain,
        })
      ) ?? [{ id: "0", text: "github.com" }],
      git_repo_url: gitSettings?.git_repo_url ?? "",
      git_repo_package_name: gitSettings?.git_repo_package_name ?? "",
    },
    mode: "onChange",
    // when a field already has an error, re-validate it on change too
    reValidateMode: "onChange",
  })
  const [pendingReplacement, setPendingReplacement] =
    useState<GitFormValues | null>(null)

  const saveGitSettings = async (data: GitFormValues) => {
    try {
      await updateGitSettings({
        requestBody: {
          git_allowed_domains: data.git_allowed_domains.map(
            (domain) => domain.text
          ),
          git_repo_url: data.git_repo_url,
          git_repo_package_name: data.git_repo_package_name,
        },
      })
    } catch {
      console.error("Failed to update Git settings")
    }
  }

  const onSubmit = async (data: GitFormValues) => {
    if (isRepositoryUrlReplaced(gitSettings?.git_repo_url, data.git_repo_url)) {
      setPendingReplacement(data)
      return
    }
    await saveGitSettings(data)
  }

  if (gitSettingsIsLoading) {
    return <CenteredSpinner />
  }
  if (gitSettingsError || !gitSettings) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading Git settings: ${gitSettingsError?.message || "Unknown error"}`}
      />
    )
  }
  const currentOrigin = gitSettings.git_repo_url
    ? toRegistryOrigin(gitSettings.git_repo_url)
    : ""

  return (
    <div className="space-y-8">
      <Form {...form}>
        <form
          id={GIT_SETTINGS_FORM_ID}
          onSubmit={form.handleSubmit(onSubmit)}
          className="space-y-8"
        >
          <FormField
            control={form.control}
            name="git_repo_url"
            render={({ field }) => (
              <FormItem className="flex flex-col">
                <FormLabel>Remote repository URL</FormLabel>
                <FormControl>
                  <Input
                    placeholder="git+ssh://someuser@git.example.com:2222/org/team/repo.git"
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
                <FormDescription className="flex flex-col gap-2">
                  <span>
                    The pip git URL of the remote repository, which uses the{" "}
                    <span className="font-mono tracking-tighter">git+ssh</span>{" "}
                    scheme. Supports nested groups and custom ports.
                  </span>
                  <span>
                    Format:{" "}
                    <span className="font-mono tracking-tight">
                      {"git+ssh://<user>@<hostname>[:<port>]/<org>/<repo>.git"}
                    </span>
                  </span>
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="git_repo_package_name"
            render={({ field }) => (
              <FormItem className="flex flex-col">
                <FormLabel>Repository package name</FormLabel>
                <FormControl>
                  <Input
                    placeholder="package_name"
                    {...field}
                    value={field.value ?? ""}
                  />
                </FormControl>
                <FormDescription>
                  Name of the python package in the repository. If not provided,
                  the repository name from the Git URL will be used.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="git_allowed_domains"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Allowed Git domains</FormLabel>
                <FormControl>
                  <CustomTagInput
                    {...field}
                    placeholder="Enter a domain..."
                    tags={field.value}
                    setTags={field.onChange}
                  />
                </FormControl>
                <FormDescription>
                  Add domains that are allowed for Git operations (e.g.,
                  github.com, gitlab.com, or gitlab.example.com:2222 with port)
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />
        </form>
      </Form>
      <OrgRegistrySshKeySection />
      <Button
        type="submit"
        form={GIT_SETTINGS_FORM_ID}
        disabled={updateGitSettingsIsPending}
      >
        Save repository settings
      </Button>
      <ConfirmDestructiveDialog
        open={pendingReplacement !== null}
        onOpenChange={(open) => {
          if (!open) {
            setPendingReplacement(null)
          }
        }}
        title="Replace repository?"
        description={
          <p>
            Saving a new URL permanently deletes the current repository with all
            of its synced versions and actions. Workflows that use its actions
            fail until the new repository is synced.
          </p>
        }
        identifier={{ label: "Current repository URL", value: currentOrigin }}
        confirmPhrase={currentOrigin}
        confirmLabel="Replace repository"
        isPending={updateGitSettingsIsPending}
        onConfirm={async () => {
          if (pendingReplacement) {
            const data = pendingReplacement
            setPendingReplacement(null)
            await saveGitSettings(data)
          }
        }}
      />
      <OrgRegistryRepositoryRemoveSection />
    </div>
  )
}

"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
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
import { useOrgGitSettings } from "@/lib/hooks"

const gitFormSchema = z.object({
  git_allowed_domains: z.array(
    z.object({
      id: z.string(),
      text: z.string().min(1, "Cannot be empty"),
    })
  ),
  git_repo_url: z
    .string()
    .nullish()
    .refine((url) => {
      if (!url) return true
      // Matches the backend regex in tracecat/git/utils.py
      // Supports:
      // - Standard format: git+ssh://git@github.com/org/repo.git
      // - With port: git+ssh://git@gitlab.example.com:2222/org/repo.git
      // - Nested groups: git+ssh://git@gitlab.com/org/team/subteam/repo.git
      // - With ref: git+ssh://git@github.com/org/repo.git@main
      // - Optional .git suffix
      // Requires at least 2 path segments (org/repo) to match backend validation
      const regex = /^git\+ssh:\/\/git@[^/]+\/[^/]+\/.+?(?:\.git)?(?:@[^/]+)?$/
      return regex.test(url)
    }, "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)")
    // Empty string signals removal
    .transform((url) => url?.trim() || null),
  git_repo_package_name: z
    .string()
    .nullish()
    // Empty string signals removal
    .transform((name) => name?.trim() || null),
})

type GitFormValues = z.infer<typeof gitFormSchema>

export function OrgSettingsGitForm() {
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
  })
  const onSubmit = async (data: GitFormValues) => {
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

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="git_repo_url"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel>Remote repository URL</FormLabel>
              <FormControl>
                <Input
                  placeholder="git+ssh://git@gitlab.example.com:2222/org/team/repo.git"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormDescription>
                Git URL of the remote repository. Must use{" "}
                <span className="font-mono tracking-tighter">git+ssh</span>{" "}
                scheme. Supports nested groups and custom ports.
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
        <Button type="submit" disabled={updateGitSettingsIsPending}>
          Update Git settings
        </Button>
      </form>
    </Form>
  )
}

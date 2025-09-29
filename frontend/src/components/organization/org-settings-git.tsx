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
import { GIT_SSH_URL_REGEX } from "@/lib/git"
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
    .superRefine((url, ctx) => {
      if (!url) return

      // Use the regex with named capture groups to provide detailed error messages
      const match = GIT_SSH_URL_REGEX.exec(url)
      if (match) return

      // Provide specific error messages based on what's missing
      if (!url.startsWith("git+ssh://")) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "URL must start with 'git+ssh://' protocol",
        })
        return
      }

      if (!url.includes("git@")) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "URL must include 'git@' user specification",
        })
        return
      }

      const afterProtocol = url.replace("git+ssh://git@", "")

      // Split by first '/' to separate hostname from path
      const firstSlashIndex = afterProtocol.indexOf("/")
      if (firstSlashIndex === -1) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "URL must include a repository path",
        })
        return
      }

      const hostname = afterProtocol.substring(0, firstSlashIndex)
      const repoPath = afterProtocol.substring(firstSlashIndex + 1)

      // Check for valid hostname
      if (!hostname) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Missing hostname",
        })
        return
      }

      if (hostname.includes(":")) {
        const colonIndex = hostname.lastIndexOf(":")
        const portPart = hostname.substring(colonIndex + 1)

        if (!portPart) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Missing port number after ':'",
          })
          return
        }

        if (!/^\d+$/.test(portPart)) {
          ctx.addIssue({
            code: z.ZodIssueCode.custom,
            message: "Port must be numeric",
          })
          return
        }
      }

      // Check that we have at least 2 segments in the repo path (org/repo)
      const pathSegments = repoPath
        .split("/")
        .filter((segment) => segment.length > 0)

      if (pathSegments.length < 2) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message:
            "Repository path must have at least 2 segments (e.g., org/repo)",
        })
        return
      }

      // Fallback for any other validation failures
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message:
          "Must be a valid Git SSH URL (e.g., git+ssh://git@github.com/org/repo.git)",
      })
    }),
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
    mode: "onChange",
    // when a field already has an error, re-validate it on change too
    reValidateMode: "onChange",
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
              <FormDescription className="flex flex-col gap-2">
                <span>
                  The pip git URL of the remote repository, which uses the{" "}
                  <span className="font-mono tracking-tighter">git+ssh</span>{" "}
                  scheme. Supports nested groups and custom ports.
                </span>
                <span>
                  Format:{" "}
                  <span className="font-mono tracking-tight">
                    {"git+ssh://git@<hostname>[:<port>]/<org>/<repo>.git"}
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
        <Button type="submit" disabled={updateGitSettingsIsPending}>
          Update Git settings
        </Button>
      </form>
    </Form>
  )
}

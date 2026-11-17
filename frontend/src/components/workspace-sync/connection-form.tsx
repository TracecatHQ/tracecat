import { z } from "zod"
import { validateGitSshUrl } from "@/lib/git"

const vcsProviderOptions = [
  "github",
  "gitlab",
  "bitbucket",
  "bitbucket_data_center",
] as const

/** Workspace sync settings: provider plus a validated git+ssh repository URL. */
export const syncSettingsSchema = z
  .object({
    git_provider: z.enum(vcsProviderOptions).default("github"),
    git_repo_url: z
      .string()
      .nullish()
      .transform((url) => url?.trim() || null)
      .superRefine((url, ctx) => validateGitSshUrl(url, ctx)),
  })
  .superRefine((values, ctx) => {
    if (
      values.git_provider === "bitbucket" &&
      values.git_repo_url &&
      !/^git\+ssh:\/\/[^@]+@bitbucket\.org\/[^/]+\/[^/]+\.git(?:@.+)?$/.test(
        values.git_repo_url
      )
    ) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["git_repo_url"],
        message: "Use a Bitbucket Cloud repository on bitbucket.org.",
      })
    }
  })

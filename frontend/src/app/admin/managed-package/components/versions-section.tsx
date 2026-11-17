"use client"

import { PlusIcon } from "lucide-react"
import { type FormEvent, useState } from "react"
import type {
  GitTagInfo,
  ManagedReleaseCandidateRead,
  ManagedTargetRead,
} from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { formatTimestamp, RELEASE_TAG_PATTERN } from "../managed-package-utils"
import { useCutRelease, useReleaseCandidate } from "../use-managed-package"

const CUT_RELEASE_FORM_ID = "managed-package-cut-release-form"

/** Props for {@link VersionsSection}. */
export interface VersionsSectionProps {
  tags: GitTagInfo[]
  targets: ManagedTargetRead[]
  isLoading: boolean
  error: unknown
  hasSource: boolean
}

/** Lists release tags with how many targets pin each, and cuts new ones. */
export function VersionsSection({
  tags,
  targets,
  isLoading,
  error,
  hasSource,
}: VersionsSectionProps) {
  const [dialogOpen, setDialogOpen] = useState(false)

  const pinnedCounts = new Map<string, number>()
  for (const target of targets) {
    pinnedCounts.set(
      target.pinned_tag,
      (pinnedCounts.get(target.pinned_tag) ?? 0) + 1
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex items-end justify-between gap-4">
        <div className="space-y-1">
          <h3 className="text-lg font-semibold">Versions</h3>
          <p className="text-sm text-muted-foreground">
            Release tags in the source repository, newest first.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setDialogOpen(true)}
          disabled={!hasSource}
        >
          <PlusIcon className="mr-1 size-3" />
          Cut release
        </Button>
      </div>

      <VersionsTable
        tags={tags}
        pinnedCounts={pinnedCounts}
        isLoading={isLoading}
        error={error}
        hasSource={hasSource}
      />

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent size="md">
          {dialogOpen && (
            <CutReleaseForm
              latestTag={tags[0]?.name ?? null}
              onDone={() => setDialogOpen(false)}
            />
          )}
        </DialogContent>
      </Dialog>
    </section>
  )
}

function VersionsTable({
  tags,
  pinnedCounts,
  isLoading,
  error,
  hasSource,
}: {
  tags: GitTagInfo[]
  pinnedCounts: Map<string, number>
  isLoading: boolean
  error: unknown
  hasSource: boolean
}) {
  if (!hasSource) {
    return (
      <div className="text-sm text-muted-foreground">
        Configure a source to see its versions.
      </div>
    )
  }
  if (isLoading) {
    return <div className="text-sm text-muted-foreground">Loading tags...</div>
  }
  if (error) {
    return (
      <div className="text-sm text-destructive">
        {getApiErrorDetail(error) ?? "Failed to load tags."}
      </div>
    )
  }
  if (tags.length === 0) {
    return (
      <div className="text-sm text-muted-foreground">
        No tags yet. Cut a release to create the first one.
      </div>
    )
  }
  return (
    <div className="rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="text-xs">Tag</TableHead>
            <TableHead className="text-xs">Commit</TableHead>
            <TableHead className="text-xs">Date</TableHead>
            <TableHead className="text-right text-xs">Pinned targets</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {tags.map((tag) => (
            <TableRow key={tag.name}>
              <TableCell className="font-mono text-xs">{tag.name}</TableCell>
              <TableCell>
                <code className="text-xs text-muted-foreground">
                  {tag.commit_sha.substring(0, 7)}
                </code>
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {formatTimestamp(tag.date)}
              </TableCell>
              <TableCell className="text-right text-xs">
                {pinnedCounts.get(tag.name) ?? 0}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

function CutReleaseForm({
  latestTag,
  onDone,
}: {
  latestTag: string | null
  onDone: () => void
}) {
  const [tag, setTag] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const cutRelease = useCutRelease()
  const { candidate, isLoading: candidateIsLoading } = useReleaseCandidate({
    enabled: true,
  })

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = tag.trim()
    if (!RELEASE_TAG_PATTERN.test(value)) {
      setFormError("Use the form vX.Y.Z, for example v1.4.0.")
      return
    }
    // A release needs a published package to deploy.
    if (!candidate?.package) {
      return
    }
    setFormError(null)
    try {
      // Bind the tag to the commit shown above, not whatever HEAD is now.
      const created = await cutRelease.mutateAsync({
        tag: value,
        commitSha: candidate.commit_sha,
      })
      toast({
        title: "Release cut",
        description: `Tagged ${created.name} at ${created.commit_sha.substring(0, 7)}.`,
      })
      onDone()
    } catch (err) {
      setFormError(getApiErrorDetail(err) ?? "Failed to cut the release.")
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Cut release</DialogTitle>
        <DialogDescription>
          Tags the latest commit on the source repository&apos;s base branch. No
          customer changes until you deploy the tag.
        </DialogDescription>
      </DialogHeader>
      <DialogBody>
        <ReleaseCandidateSummary
          candidate={candidate}
          isLoading={candidateIsLoading}
        />
        <form
          id={CUT_RELEASE_FORM_ID}
          onSubmit={handleSubmit}
          className="space-y-2"
        >
          <Label htmlFor="managed-release-tag">Tag name</Label>
          <Input
            id="managed-release-tag"
            value={tag}
            onChange={(event) => setTag(event.target.value)}
            placeholder="v1.0.0"
            className="font-mono text-xs"
            autoFocus
          />
          {latestTag && (
            <p className="text-xs text-muted-foreground">
              Latest tag: <span className="font-mono">{latestTag}</span>
            </p>
          )}
          {formError && (
            <p className="whitespace-pre-wrap text-xs text-destructive">
              {formError}
            </p>
          )}
        </form>
      </DialogBody>
      <DialogFooter>
        <Button
          size="sm"
          type="submit"
          form={CUT_RELEASE_FORM_ID}
          disabled={
            cutRelease.isPending || tag.trim() === "" || !candidate?.package
          }
        >
          {cutRelease.isPending ? "Tagging..." : "Cut release"}
        </Button>
      </DialogFooter>
    </>
  )
}

/**
 * Shows exactly what a release would tag: the base branch commit and the
 * package published in it.
 */
function ReleaseCandidateSummary({
  candidate,
  isLoading,
}: {
  candidate: ManagedReleaseCandidateRead | undefined
  isLoading: boolean
}) {
  if (isLoading) {
    return (
      <p className="mb-4 text-xs text-muted-foreground">
        Reading the source repository...
      </p>
    )
  }
  if (!candidate) {
    return (
      <p className="mb-4 text-xs text-destructive">
        Could not read the source repository&apos;s base branch.
      </p>
    )
  }
  const pkg = candidate.package
  const existingTags = candidate.existing_tags ?? []
  const explicit = pkg?.explicit ?? []
  const implied = pkg?.implied ?? []
  const registry = pkg?.registry ?? []
  return (
    <div className="mb-4 space-y-2 rounded-md border px-3 py-2 text-xs">
      <p>
        <span className="text-muted-foreground">Commit </span>
        <code>{candidate.commit_sha.substring(0, 7)}</code>
        <span className="text-muted-foreground"> on </span>
        <span className="font-medium">{candidate.branch}</span>
        <span className="text-muted-foreground">
          {" "}
          by {candidate.commit_author}, {formatTimestamp(candidate.commit_date)}
        </span>
      </p>
      <p className="text-muted-foreground">{candidate.commit_message}</p>
      {existingTags.length > 0 && (
        <p className="text-amber-700 dark:text-amber-400">
          This commit is already released as {existingTags.join(", ")}. A new
          tag would ship the same package.
        </p>
      )}
      {pkg ? (
        <div className="space-y-1">
          <p>
            Package: {explicit.length} selected, {implied.length} included as
            dependencies
            {registry.length > 0 &&
              `, ${registry.length} custom registry ${registry.length === 1 ? "repository" : "repositories"}`}
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {explicit.map((entry) => (
              <li
                key={`${entry.resource_type}:${entry.source_id}`}
                className="rounded border px-1.5 py-0.5 font-mono"
              >
                {entry.resource_type}/{entry.source_id}
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="text-destructive">
          No package is published on this commit. Publish one from the source
          workspace&apos;s Git sync page first.
        </p>
      )}
    </div>
  )
}

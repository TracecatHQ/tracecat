"use client"

import { PlusIcon } from "lucide-react"
import { type FormEvent, useState } from "react"
import type { GitTagInfo, ManagedTargetRead } from "@/client"
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
import { useCutRelease } from "../use-managed-package"

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
        <DialogContent size="sm">
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

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = tag.trim()
    if (!RELEASE_TAG_PATTERN.test(value)) {
      setFormError("Use the form vX.Y.Z, for example v1.4.0.")
      return
    }
    setFormError(null)
    try {
      const created = await cutRelease.mutateAsync(value)
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
          Tags the head of the source repository&apos;s base branch.
        </DialogDescription>
      </DialogHeader>
      <DialogBody>
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
          disabled={cutRelease.isPending || tag.trim() === ""}
        >
          {cutRelease.isPending ? "Tagging..." : "Cut release"}
        </Button>
      </DialogFooter>
    </>
  )
}

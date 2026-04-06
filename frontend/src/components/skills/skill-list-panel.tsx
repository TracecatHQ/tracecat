"use client"

import { AlertCircle, Bot, Copy, Loader2, Plus, Upload } from "lucide-react"
import { type ChangeEvent, type DragEvent, useRef } from "react"
import type { SkillRead } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { TracecatApiError } from "@/lib/errors"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

type SkillListPanelProps = {
  workspaceId: string
  activeSkillId: string | null
  search: string
  onSearchChange: (value: string) => void
  visibleSkills: SkillRead[]
  skillsLoading: boolean
  skillsError: TracecatApiError | null
  isDragOver: boolean
  onDragOver: (event: DragEvent<HTMLDivElement>) => void
  onDragLeave: () => void
  onDrop: (event: DragEvent<HTMLDivElement>) => void
  onDirectoryInput: (event: ChangeEvent<HTMLInputElement>) => void
  uploadSkillPending: boolean
  onSelectSkill: (skillId: string) => void
  onCopyLocalAgentPrompt: () => Promise<void>
  onOpenNewSkillDialog: () => void
}

/**
 * Left sidebar listing workspace skills with search, upload, and creation.
 *
 * @param props Panel state and callbacks from the parent hook.
 */
export function SkillListPanel({
  workspaceId,
  activeSkillId,
  search,
  onSearchChange,
  visibleSkills,
  skillsLoading,
  skillsError,
  isDragOver,
  onDragOver,
  onDragLeave,
  onDrop,
  onDirectoryInput,
  uploadSkillPending,
  onSelectSkill,
  onCopyLocalAgentPrompt,
  onOpenNewSkillDialog,
}: SkillListPanelProps) {
  const createInputRef = useRef<HTMLInputElement>(null)

  return (
    <div className="flex h-full flex-col">
      <div className="space-y-3 border-b p-4">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-base font-semibold">Skills</h2>
            <p className="text-sm text-muted-foreground">
              Upload, edit, publish, and test workspace skills.
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={onOpenNewSkillDialog}>
            <Plus className="mr-2 size-4" />
            New
          </Button>
        </div>

        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search skills"
        />

        <div
          className={cn(
            "rounded-md border border-dashed p-3 text-sm transition-colors",
            isDragOver
              ? "border-foreground bg-muted/60"
              : "border-muted-foreground/25"
          )}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={(event) => void onDrop(event)}
        >
          <div className="flex items-center gap-2">
            <Upload className="size-4 text-muted-foreground" />
            <span className="font-medium">Upload from computer</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Drag a skill directory here or choose a folder.
          </p>
          <Button
            size="sm"
            variant="secondary"
            className="mt-3"
            onClick={() => createInputRef.current?.click()}
            disabled={uploadSkillPending}
          >
            {uploadSkillPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Upload className="mr-2 size-4" />
            )}
            Choose folder
          </Button>
        </div>

        <div className="rounded-md border p-3 text-sm">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Bot className="size-4 text-muted-foreground" />
              <span className="font-medium">Upload via local agent</span>
            </div>
            <Button
              size="icon"
              variant="ghost"
              onClick={() => void onCopyLocalAgentPrompt()}
            >
              <Copy className="size-4" />
            </Button>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Workspace ID: <span className="font-mono">{workspaceId}</span>
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            Local agents can call the Tracecat{" "}
            <span className="font-mono">upload_skill</span> MCP tool to push a
            local directory directly into this workspace.
          </p>
        </div>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-2">
          {skillsLoading ? (
            <div className="flex h-24 items-center justify-center">
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            </div>
          ) : skillsError ? (
            <Alert variant="destructive">
              <AlertCircle className="size-4" />
              <AlertTitle>Failed to load skills</AlertTitle>
              <AlertDescription>
                {getApiErrorDetail(skillsError) ?? "Please try again."}
              </AlertDescription>
            </Alert>
          ) : visibleSkills.length === 0 ? (
            <div className="px-2 py-6 text-sm text-muted-foreground">
              No skills yet.
            </div>
          ) : (
            <div className="space-y-1">
              {visibleSkills.map((listedSkill) => {
                const isActive = listedSkill.id === activeSkillId
                return (
                  <button
                    key={listedSkill.id}
                    type="button"
                    onClick={() => onSelectSkill(listedSkill.id)}
                    className={cn(
                      "w-full rounded-md border px-3 py-2 text-left transition-colors",
                      isActive
                        ? "border-foreground bg-muted"
                        : "border-transparent hover:border-border hover:bg-muted/50"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">
                        {listedSkill.title ?? listedSkill.slug}
                      </span>
                      {listedSkill.current_version ? (
                        <Badge variant="outline">
                          v{listedSkill.current_version.version}
                        </Badge>
                      ) : (
                        <Badge variant="secondary">Unpublished</Badge>
                      )}
                    </div>
                    <p className="truncate text-xs text-muted-foreground">
                      {listedSkill.slug}
                    </p>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </ScrollArea>

      <input
        ref={createInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => void onDirectoryInput(event)}
        {...({
          directory: "",
          webkitdirectory: "",
        } as Record<string, string>)}
      />
    </div>
  )
}

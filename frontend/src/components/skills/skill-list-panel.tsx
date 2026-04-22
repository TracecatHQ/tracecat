"use client"

import {
  AlertCircle,
  ChevronDown,
  FilePlus,
  Loader2,
  Plus,
  SearchX,
  Upload,
} from "lucide-react"
import type { SkillReadMinimal } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ScrollArea } from "@/components/ui/scroll-area"
import type { TracecatApiError } from "@/lib/errors"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

type SkillListPanelProps = {
  activeSkillId: string | null
  search: string
  onSearchChange: (value: string) => void
  visibleSkills: SkillReadMinimal[]
  skillsLoading: boolean
  skillsError: TracecatApiError | null
  onSelectSkill: (skillId: string) => void
  onOpenNewSkillDialog: () => void
  onOpenUploadSkillDialog: () => void
}

/**
 * Left sidebar listing workspace skills with search and a create/upload menu.
 *
 * @param props Panel state and callbacks from the parent hook.
 */
export function SkillListPanel({
  activeSkillId,
  search,
  onSearchChange,
  visibleSkills,
  skillsLoading,
  skillsError,
  onSelectSkill,
  onOpenNewSkillDialog,
  onOpenUploadSkillDialog,
}: SkillListPanelProps) {
  return (
    <div className="flex h-full flex-col">
      <div className="space-y-3 border-b p-4">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold">Skills</h2>
            <p className="truncate text-sm text-muted-foreground">
              Author and publish skills.
            </p>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost">
                <Plus className="mr-1 size-3.5" />
                New
                <ChevronDown className="ml-1 size-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              className="
                [&_[data-radix-collection-item]]:flex
                [&_[data-radix-collection-item]]:items-center
                [&_[data-radix-collection-item]]:gap-2
              "
            >
              <DropdownMenuItem onSelect={onOpenNewSkillDialog}>
                <FilePlus className="size-4 text-foreground/80" />
                <div className="flex flex-col text-xs">
                  <span>Skill</span>
                  <span className="text-xs text-muted-foreground">
                    Start from scratch
                  </span>
                </div>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={onOpenUploadSkillDialog}>
                <Upload className="size-4 text-foreground/80" />
                <div className="flex flex-col text-xs">
                  <span>Upload</span>
                  <span className="text-xs text-muted-foreground">
                    Upload an existing skill directory
                  </span>
                </div>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="Search skills"
        />
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
            search.trim() ? (
              <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
                <SearchX className="size-5 text-muted-foreground" />
                <p className="text-xs text-muted-foreground">
                  No skills match &ldquo;{search.trim()}&rdquo;
                </p>
              </div>
            ) : (
              <div className="space-y-1 px-6 py-12 text-center">
                <p className="text-sm font-medium">No skills yet</p>
                <p className="text-xs text-muted-foreground">
                  Create one from scratch or upload an existing skill directory.
                </p>
              </div>
            )
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
                        ? "border-primary bg-accent"
                        : "border-transparent hover:border-border hover:bg-muted/50"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">
                        {listedSkill.name}
                      </span>
                      {listedSkill.current_version_id ? (
                        <Badge variant="outline">Published</Badge>
                      ) : (
                        <Badge variant="secondary">Unpublished</Badge>
                      )}
                    </div>
                    <p className="line-clamp-2 text-xs text-muted-foreground">
                      {listedSkill.description?.trim() || "No description"}
                    </p>
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  )
}

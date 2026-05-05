"use client"

import { format } from "date-fns"
import {
  CircleCheck,
  CircleDot,
  Clock3,
  Copy,
  Pyramid,
  Trash2,
} from "lucide-react"
import { useRouter } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import type { SkillReadMinimal } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { DeleteSkillDialog } from "@/components/skills/delete-skill-dialog"
import {
  DEFAULT_SKILL_SORT,
  SkillsHeader,
  type SkillsSortValue,
} from "@/components/skills/skills-header"
import { Badge } from "@/components/ui/badge"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Empty,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useDeleteSkill, useSkills } from "@/hooks/use-skills"
import { cn } from "@/lib/utils"

const DEFAULT_LIMIT = 20
const ROW_NAME_COLUMN_CLASS = "min-w-0 w-[340px] shrink-0 truncate text-xs"

function getRelativeDateLabel(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return "0m"
  }

  const diffMs = Math.max(0, Date.now() - timestamp)
  const minuteMs = 60_000
  const hourMs = 60 * minuteMs
  const dayMs = 24 * hourMs
  const monthMs = 30 * dayMs
  const yearMs = 365 * dayMs

  if (diffMs < hourMs) {
    return `${Math.max(1, Math.floor(diffMs / minuteMs))}m`
  }
  if (diffMs < dayMs) {
    return `${Math.max(1, Math.floor(diffMs / hourMs))}hr`
  }
  if (diffMs < monthMs) {
    return `${Math.max(1, Math.floor(diffMs / dayMs))}d`
  }
  if (diffMs < yearMs) {
    return `${Math.max(1, Math.floor(diffMs / monthMs))}mo`
  }
  return `${Math.max(1, Math.floor(diffMs / yearMs))}y`
}

function SkillMetadataBadges({ skill }: { skill: SkillReadMinimal }) {
  return (
    <div className="flex shrink-0 items-center gap-1">
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant="secondary"
            className="h-5 cursor-default px-2 text-[10px] font-normal"
          >
            <Clock3 className="mr-1 size-3" />
            {getRelativeDateLabel(skill.updated_at)}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {format(new Date(skill.updated_at), "PPpp")}
        </TooltipContent>
      </Tooltip>

      {skill.current_version_id ? (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleCheck className="mr-1 size-3" />
          Published
        </Badge>
      ) : (
        <Badge variant="secondary" className="h-5 px-2 text-[10px] font-normal">
          <CircleDot className="mr-1 size-3" />
          Unpublished
        </Badge>
      )}
    </div>
  )
}

function SkillsListRow({
  skill,
  onOpenSkill,
  onDeleteSkill,
}: {
  skill: SkillReadMinimal
  onOpenSkill: (skillId: string) => void
  onDeleteSkill: (skill: SkillReadMinimal) => void
}) {
  const [isContextMenuOpen, setIsContextMenuOpen] = useState(false)

  const handleCopyId = (event: React.SyntheticEvent) => {
    event.stopPropagation()
    navigator.clipboard.writeText(skill.id)
    toast({
      title: "Copied",
      description: `Skill ID ${skill.id} copied to clipboard.`,
    })
  }

  return (
    <ContextMenu onOpenChange={setIsContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            "group/item flex items-center gap-2 border-b border-border px-4 py-3 transition-colors hover:bg-muted/50",
            isContextMenuOpen && "bg-muted/70"
          )}
        >
          <button
            type="button"
            onClick={() => onOpenSkill(skill.id)}
            className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
          >
            <Pyramid className="size-4 shrink-0 text-primary" />
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className={ROW_NAME_COLUMN_CLASS}>{skill.name}</span>
              <div className="flex min-w-0 flex-1 items-center justify-end gap-2 overflow-hidden">
                <SkillMetadataBadges skill={skill} />
              </div>
            </div>
          </button>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        <ContextMenuItem className="text-xs" onClick={handleCopyId}>
          <Copy className="mr-2 size-3.5" />
          Copy skill ID
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={(event) => {
            event.stopPropagation()
            onDeleteSkill(skill)
          }}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete skill
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

function compareSkills(
  a: SkillReadMinimal,
  b: SkillReadMinimal,
  sortBy: SkillsSortValue
): number {
  const direction = sortBy.direction === "asc" ? 1 : -1

  if (sortBy.field === "name") {
    return (
      a.name.localeCompare(b.name, undefined, {
        numeric: true,
        sensitivity: "base",
      }) * direction
    )
  }

  const aValue = a[sortBy.field]
  const bValue = b[sortBy.field]
  const aTimestamp = aValue ? new Date(aValue).getTime() : Number.NaN
  const bTimestamp = bValue ? new Date(bValue).getTime() : Number.NaN

  if (Number.isNaN(aTimestamp) && Number.isNaN(bTimestamp)) {
    return 0
  }
  if (Number.isNaN(aTimestamp)) {
    return 1
  }
  if (Number.isNaN(bTimestamp)) {
    return -1
  }
  return (aTimestamp - bTimestamp) * direction
}

function SkillsDashboardContent({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId)
  const { deleteSkill, deleteSkillPending } = useDeleteSkill(workspaceId)

  const [searchQuery, setSearchQuery] = useState("")
  const [sortBy, setSortBy] = useState<SkillsSortValue>(DEFAULT_SKILL_SORT)
  const [limit, setLimit] = useState(DEFAULT_LIMIT)
  const [page, setPage] = useState(0)
  const [skillToDelete, setSkillToDelete] = useState<SkillReadMinimal | null>(
    null
  )

  const filteredSkills = useMemo(() => {
    const query = searchQuery.trim().toLowerCase()
    const list = skills ?? []
    if (!query) {
      return list
    }
    return list.filter((skill) => {
      return (
        skill.name.toLowerCase().includes(query) ||
        (skill.description ?? "").toLowerCase().includes(query)
      )
    })
  }, [searchQuery, skills])

  const sortedSkills = useMemo(() => {
    return [...filteredSkills].sort((a, b) => compareSkills(a, b, sortBy))
  }, [filteredSkills, sortBy])

  const totalCount = sortedSkills.length
  const maxPage = Math.max(Math.ceil(totalCount / limit) - 1, 0)
  const safePage = Math.min(page, maxPage)
  const visibleSkills = sortedSkills.slice(
    safePage * limit,
    safePage * limit + limit
  )

  useEffect(() => {
    setPage(0)
  }, [searchQuery, sortBy, limit])

  const hasPreviousPage = safePage > 0
  const hasNextPage = safePage < maxPage

  const handleOpenSkill = (skillId: string) => {
    router.push(`/workspaces/${workspaceId}/skills/${skillId}`)
  }

  const handleConfirmDeleteSkill = async () => {
    if (!skillToDelete) {
      return
    }
    try {
      await deleteSkill({ skillId: skillToDelete.id })
      setSkillToDelete(null)
    } catch {
      // The mutation hook reports delete failures.
    }
  }

  return (
    <TooltipProvider>
      <div className="flex size-full flex-col overflow-hidden">
        <SkillsHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          sortBy={sortBy}
          onSortByChange={setSortBy}
          totalCount={totalCount}
          countLabel="skills"
          limit={limit}
          onLimitChange={setLimit}
          hasPreviousPage={hasPreviousPage}
          hasNextPage={hasNextPage}
          onPreviousPage={() => setPage((current) => Math.max(0, current - 1))}
          onNextPage={() =>
            setPage((current) => Math.min(maxPage, current + 1))
          }
          isPaginationLoading={skillsLoading}
        />

        <div className="min-h-0 flex-1 overflow-auto">
          {skillsLoading ? (
            <div className="flex h-full items-center justify-center">
              <CenteredSpinner />
            </div>
          ) : skillsError ? (
            <div className="flex h-full items-center justify-center px-6">
              <span className="text-sm text-destructive">
                Failed to load skills.
              </span>
            </div>
          ) : visibleSkills.length === 0 ? (
            <div className="flex h-full p-6">
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <Pyramid className="size-5 text-muted-foreground/60" />
                  </EmptyMedia>
                  <EmptyTitle>
                    {searchQuery.trim()
                      ? "No skills match your search"
                      : "No skills yet"}
                  </EmptyTitle>
                </EmptyHeader>
              </Empty>
            </div>
          ) : (
            <div className={cn("divide-y")}>
              {visibleSkills.map((skill) => (
                <SkillsListRow
                  key={skill.id}
                  skill={skill}
                  onOpenSkill={handleOpenSkill}
                  onDeleteSkill={setSkillToDelete}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      <DeleteSkillDialog
        open={skillToDelete !== null}
        onOpenChange={(open) => {
          if (!open) {
            setSkillToDelete(null)
          }
        }}
        skill={skillToDelete}
        pending={deleteSkillPending}
        onConfirm={handleConfirmDeleteSkill}
      />
    </TooltipProvider>
  )
}

/**
 * Workspace skills dashboard. Mirrors the workflows list layout: a search +
 * sort header followed by a flat list of skill rows. The "Create new"
 * dropdown lives in the global controls header.
 *
 * @param props.workspaceId Current workspace identifier.
 * @returns The skills list view.
 */
export function SkillsDashboard({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  useEffect(() => {
    if (!isLoading && !agentAddonsEnabled) {
      router.replace(`/workspaces/${workspaceId}`)
    }
  }, [agentAddonsEnabled, isLoading, router, workspaceId])

  if (isLoading) {
    return <div className="size-full animate-pulse bg-muted/20" />
  }

  if (!agentAddonsEnabled) {
    return null
  }

  return <SkillsDashboardContent workspaceId={workspaceId} />
}

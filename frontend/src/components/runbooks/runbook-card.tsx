"use client"

import { formatDistanceToNow } from "date-fns"
import { FileText, MoreHorizontal, Trash2 } from "lucide-react"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { RunbookRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useWorkspaceId } from "@/providers/workspace-id"

interface RunbookCardProps {
  runbook: RunbookRead
  onDelete: (runbookId: string) => void
}

export function RunbookCard({ runbook, onDelete }: RunbookCardProps) {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const [showActions, setShowActions] = useState(false)

  const handleCardClick = () => {
    router.push(`/workspaces/${workspaceId}/runbooks/${runbook.id}`)
  }

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation()
    onDelete(runbook.id)
  }

  // Extract preview text from instructions
  const getPreviewText = () => {
    if (!runbook.instructions) {
      return ""
    }
    // Extract first few lines of instructions as preview
    const lines = runbook.instructions.split("\n").filter((line) => line.trim())
    if (lines.length === 0) {
      return ""
    }
    return (
      lines.slice(0, 3).join(" ").substring(0, 150) +
      (lines.length > 3 ? "..." : "")
    )
  }

  return (
    <Card
      className="cursor-pointer transition-all hover:shadow-md relative group"
      onClick={handleCardClick}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <FileText className="h-4 w-4 text-muted-foreground flex-shrink-0" />
            <CardTitle className="text-base font-medium truncate">
              {runbook.title}
            </CardTitle>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
              <Button
                variant="ghost"
                size="icon"
                className={`h-8 w-8 transition-opacity ${
                  showActions ? "opacity-100" : "opacity-0"
                }`}
              >
                <MoreHorizontal className="h-4 w-4" />
                <span className="sr-only">Open menu</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem
                onClick={handleDelete}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="mr-2 h-4 w-4" />
                Delete runbook
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {runbook.alias && (
          <div className="mt-1">
            <Badge variant="secondary" className="font-mono text-xs">
              {runbook.alias}
            </Badge>
          </div>
        )}
        <CardDescription className="text-xs text-muted-foreground mt-1">
          Last edited{" "}
          {formatDistanceToNow(new Date(runbook.updated_at), {
            addSuffix: true,
          })}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="text-sm text-muted-foreground line-clamp-3">
          {getPreviewText()}
        </div>
      </CardContent>
    </Card>
  )
}

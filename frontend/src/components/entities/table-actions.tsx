"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { CheckCircle, Copy, Eye, Pencil, Trash2, XCircle } from "lucide-react"
import Link from "next/link"
import type { EntityFieldRead, EntityRead } from "@/client"
import { ActiveDialog } from "@/components/entities/table-common"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceId } from "@/providers/workspace-id"

export function EntityActions({
  entity,
  setSelectedEntity,
  setActiveDialog,
  onReactivateEntity,
  onEdit,
}: {
  entity: EntityRead
  setSelectedEntity: (e: EntityRead) => void
  setActiveDialog: (dialog: ActiveDialog | null) => void
  onReactivateEntity?: (entityId: string) => Promise<void>
  onEdit?: (e: EntityRead) => void
}) {
  const workspaceId = useWorkspaceId()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="size-8 p-0"
          onClick={(e) => e.stopPropagation()}
        >
          <span className="sr-only">Open menu</span>
          <DotsHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onClick={(e) => {
            e.stopPropagation()
            navigator.clipboard.writeText(entity.id)
            toast({ title: "Copied", description: "Entity ID copied." })
          }}
        >
          <Copy className="mr-2 h-3 w-3" />
          Copy entity ID
        </DropdownMenuItem>

        {entity.is_active && (
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation()
            }}
            asChild
          >
            <Link href={`/workspaces/${workspaceId}/entities/${entity.id}`}>
              <Eye className="mr-2 h-3 w-3" />
              View fields
            </Link>
          </DropdownMenuItem>
        )}

        {entity.is_active && onEdit && (
          <DropdownMenuItem
            onClick={(e) => {
              e.stopPropagation()
              onEdit(entity)
            }}
          >
            <Pencil className="mr-2 h-3 w-3" />
            Edit entity
          </DropdownMenuItem>
        )}

        {entity.is_active ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-rose-500 focus:text-rose-600"
              onClick={(e) => {
                e.stopPropagation()
                setSelectedEntity(entity)
                setActiveDialog(ActiveDialog.EntityArchive)
              }}
            >
              <XCircle className="mr-2 h-3 w-3" />
              Archive entity
            </DropdownMenuItem>
          </>
        ) : (
          <>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation()
                onReactivateEntity?.(entity.id).catch((error) => {
                  console.error("Failed to reactivate entity:", error)
                })
              }}
            >
              <CheckCircle className="mr-2 h-3 w-3" />
              Restore entity
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-rose-500 focus:text-rose-600"
              onClick={(e) => {
                e.stopPropagation()
                setSelectedEntity(entity)
                setActiveDialog(ActiveDialog.EntityDelete)
              }}
            >
              <Trash2 className="mr-2 h-3 w-3" />
              Delete entity
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function EntityFieldActions({
  field,
  setSelectedField,
  setActiveDialog,
  onReactivateField,
  onEdit,
}: {
  field: EntityFieldRead
  setSelectedField: (f: EntityFieldRead) => void
  setActiveDialog: (dialog: ActiveDialog | null) => void
  onReactivateField?: (fieldId: string) => Promise<void>
  onEdit?: (f: EntityFieldRead) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="size-8 p-0">
          <span className="sr-only">Open menu</span>
          <DotsHorizontalIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem
          onClick={() => navigator.clipboard.writeText(field.id)}
        >
          <Copy className="mr-2 h-3 w-3" />
          Copy field ID
        </DropdownMenuItem>

        {onEdit && (
          <DropdownMenuItem onClick={() => onEdit(field)}>
            <Pencil className="mr-2 h-3 w-3" />
            Edit field
          </DropdownMenuItem>
        )}

        {field.is_active ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-rose-500 focus:text-rose-600"
              onClick={() => {
                setSelectedField(field)
                setActiveDialog(ActiveDialog.FieldArchive)
              }}
            >
              <XCircle className="mr-2 h-3 w-3" />
              Archive field
            </DropdownMenuItem>
          </>
        ) : (
          <>
            <DropdownMenuItem
              onClick={() => void onReactivateField?.(field.id)}
            >
              <CheckCircle className="mr-2 h-3 w-3" />
              Restore field
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-rose-500 focus:text-rose-600"
              onClick={() => {
                setSelectedField(field)
                setActiveDialog(ActiveDialog.FieldDelete)
              }}
            >
              <Trash2 className="mr-2 h-3 w-3" />
              Delete field
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

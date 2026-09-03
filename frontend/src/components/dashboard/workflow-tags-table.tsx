"use client"

import type { TagRead, TagUpdate } from "@/client"
import { TagCatalogTable } from "@/components/tags/tag-catalog-table"

interface WorkflowTagsTableProps {
  tags: TagRead[]
  onDeleteTag: (tagId: string) => Promise<void>
  onUpdateTag: (tagId: string, params: TagUpdate) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
}

export function WorkflowTagsTable(props: WorkflowTagsTableProps) {
  return <TagCatalogTable {...props} resourceLabel="workflows" />
}

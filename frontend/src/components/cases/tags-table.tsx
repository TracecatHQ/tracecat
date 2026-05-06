"use client"

import type { CaseTagRead, TagUpdate } from "@/client"
import { TagCatalogTable } from "@/components/tags/tag-catalog-table"

interface TagsTableProps {
  tags: CaseTagRead[]
  onDeleteTag: (tagId: string) => Promise<void>
  onUpdateTag: (tagId: string, params: TagUpdate) => Promise<void>
  isDeleting?: boolean
  isUpdating?: boolean
}

export function TagsTable(props: TagsTableProps) {
  return <TagCatalogTable {...props} resourceLabel="cases" />
}

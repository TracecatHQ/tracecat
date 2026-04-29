"use client"

import {
  BotIcon,
  FileCogIcon,
  FileJsonIcon,
  FileTextIcon,
  FolderCheckIcon,
  FolderIcon,
  FolderPlusIcon,
  KeyRoundIcon,
  type LucideIcon,
  PuzzleIcon,
  ServerIcon,
  ShieldIcon,
  SparklesIcon,
  WebhookIcon,
} from "lucide-react"
import type { SpmInventoryItemType, SpmInventorySourceType } from "@/client"

export const ITEM_TYPE_ICONS: Record<SpmInventoryItemType, LucideIcon> = {
  hook: WebhookIcon,
  plugin: PuzzleIcon,
  mcp_server: ServerIcon,
  instruction_file: FileTextIcon,
  permission_config: KeyRoundIcon,
  sandbox_config: ShieldIcon,
  trusted_directory: FolderCheckIcon,
  additional_directory: FolderPlusIcon,
  skill: SparklesIcon,
  agent: BotIcon,
}

export const SOURCE_TYPE_ICONS: Record<SpmInventorySourceType, LucideIcon> = {
  settings_json: FileCogIcon,
  settings_local_json: FileCogIcon,
  claude_json: FileJsonIcon,
  hooks_json: WebhookIcon,
  mcp_json: ServerIcon,
  claude_md: FileTextIcon,
  claude_local_md: FileTextIcon,
  agents_md: FileTextIcon,
  skill_frontmatter: SparklesIcon,
  agent_frontmatter: BotIcon,
  plugin_manifest: PuzzleIcon,
  directory: FolderIcon,
}

export function itemTypeLabel(itemType: SpmInventoryItemType): string {
  return itemType
}

export function sourceTypeLabel(sourceType: SpmInventorySourceType): string {
  return sourceType
}

export function itemTypeIcon(itemType: SpmInventoryItemType): LucideIcon {
  return ITEM_TYPE_ICONS[itemType]
}

export function sourceTypeIcon(sourceType: SpmInventorySourceType): LucideIcon {
  return SOURCE_TYPE_ICONS[sourceType]
}

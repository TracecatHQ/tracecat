"use client"

import {
  ActivityIcon,
  BinaryIcon,
  BotIcon,
  BracesIcon,
  FileCogIcon,
  FileJsonIcon,
  FileTextIcon,
  FolderCheckIcon,
  FolderIcon,
  FolderPlusIcon,
  KeyRoundIcon,
  type LucideIcon,
  PaletteIcon,
  PuzzleIcon,
  ServerIcon,
  ShieldIcon,
  SparklesIcon,
  SwatchBookIcon,
  TerminalIcon,
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
  subagent: BotIcon,
  command: TerminalIcon,
  lsp_server: BracesIcon,
  monitor: ActivityIcon,
  binary: BinaryIcon,
  plugin_settings: FileCogIcon,
  output_style: PaletteIcon,
  theme: SwatchBookIcon,
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
  subagent_frontmatter: BotIcon,
  plugin_manifest: PuzzleIcon,
  command_file: TerminalIcon,
  lsp_json: BracesIcon,
  monitors_json: ActivityIcon,
  binary_file: BinaryIcon,
  plugin_settings_json: FileCogIcon,
  output_style_file: PaletteIcon,
  theme_file: SwatchBookIcon,
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

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
import type { SpmArtifactType, SpmAssetType } from "@/client"
import { formatLabel } from "./spm-common"

export const ASSET_TYPE_ICONS: Record<SpmAssetType, LucideIcon> = {
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

export const ARTIFACT_TYPE_ICONS: Record<SpmArtifactType, LucideIcon> = {
  "settings.json": FileCogIcon,
  "settings.local.json": FileCogIcon,
  ".claude.json": FileJsonIcon,
  "hooks.json": WebhookIcon,
  ".mcp.json": ServerIcon,
  "CLAUDE.md": FileTextIcon,
  "CLAUDE.local.md": FileTextIcon,
  "AGENTS.md": FileTextIcon,
  "skill-frontmatter": SparklesIcon,
  "agent-frontmatter": BotIcon,
  "plugin.json": PuzzleIcon,
  directory: FolderIcon,
}

export function assetTypeLabel(assetType: SpmAssetType): string {
  if (assetType === "mcp_server") return "MCP server"
  return formatLabel(assetType)
}

export function artifactTypeLabel(artifactType: SpmArtifactType): string {
  if (artifactType === "skill-frontmatter") return "Skill frontmatter"
  if (artifactType === "agent-frontmatter") return "Agent frontmatter"
  return artifactType
}

export function assetTypeIcon(assetType: SpmAssetType): LucideIcon {
  return ASSET_TYPE_ICONS[assetType]
}

export function artifactTypeIcon(artifactType: SpmArtifactType): LucideIcon {
  return ARTIFACT_TYPE_ICONS[artifactType]
}

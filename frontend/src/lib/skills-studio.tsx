import { AlertCircle, CheckCircle2 } from "lucide-react"
import type { DragEvent, ReactNode } from "react"

import { Badge } from "@/components/ui/badge"

const EDITABLE_EXTENSIONS = [
  ".md",
  ".py",
  ".txt",
  ".js",
  ".mjs",
  ".cjs",
  ".ts",
  ".jsx",
  ".tsx",
  ".html",
  ".htm",
  ".css",
  ".json",
  ".yaml",
  ".yml",
] as const

export type DraftChange =
  | { kind: "text"; content: string; contentType: string }
  | { kind: "upload"; file: File; contentType: string }
  | { kind: "delete" }

export type VisibleFileEntry = {
  path: string
  contentType: string
  sizeBytes: number
  change: DraftChange | null
  isNew: boolean
}

export type SkillFileTreeNode =
  | {
      kind: "folder"
      name: string
      path: string
      children: SkillFileTreeNode[]
    }
  | {
      kind: "file"
      name: string
      path: string
      file: VisibleFileEntry
    }

/**
 * Checks whether a file path can be edited in the studio UI.
 */
export function isEditablePath(path: string): boolean {
  return EDITABLE_EXTENSIONS.some((extension) => path.endsWith(extension))
}

/**
 * Checks whether a file is markdown by extension.
 */
export function isMarkdownPath(path: string): boolean {
  return path.endsWith(".md")
}

const SKILL_SLUG_PATTERN = /^[a-z0-9]+(-[a-z0-9]+)*$/
const SKILL_SLUG_MAX_LENGTH = 64

/**
 * Validates a skill slug against the Agent Skills `name` rules: kebab-case
 * (lowercase letters, digits, and single hyphens), 1-64 characters.
 *
 * Returns a human-readable error message, or null if the slug is valid.
 */
export function validateSkillSlug(slug: string): string | null {
  const trimmed = slug.trim()
  if (trimmed.length === 0) {
    return "Slug is required."
  }
  if (trimmed.length > SKILL_SLUG_MAX_LENGTH) {
    return `Slug must be ${SKILL_SLUG_MAX_LENGTH} characters or fewer.`
  }
  if (!SKILL_SLUG_PATTERN.test(trimmed)) {
    return "Use lowercase letters, numbers, and single hyphens (e.g. threat-intel)."
  }
  return null
}

/**
 * Computes content-type header for editor-generated content.
 */
export function getTextContentType(path: string): string {
  if (path.endsWith(".md")) {
    return "text/markdown; charset=utf-8"
  }
  if (path.endsWith(".py")) {
    return "text/x-python; charset=utf-8"
  }
  return "text/plain; charset=utf-8"
}

/**
 * Resolves the editor language from file extension.
 */
export function getLanguageForPath(path: string): string {
  if (path.endsWith(".py")) {
    return "python"
  }
  if (path.endsWith(".json")) {
    return "json"
  }
  if (path.endsWith(".yaml") || path.endsWith(".yml")) {
    return "yaml"
  }
  return "text"
}

/**
 * Sorts paths lexicographically for stable file ordering.
 */
export function comparePaths(left: string, right: string): number {
  return left.localeCompare(right)
}

/**
 * Attempts to infer a shared upload root for a batch of paths.
 */
export function getUploadRootName(paths: string[]): string | null {
  if (paths.length === 0) {
    return null
  }

  const segments = paths.map((path) => path.split("/").filter(Boolean))
  const firstRoot = segments[0]?.[0]
  if (!firstRoot) {
    return null
  }
  if (segments.some((parts) => parts.length < 2 || parts[0] !== firstRoot)) {
    return null
  }
  return firstRoot
}

/**
 * Encodes binary file data for upload payloads.
 */
async function arrayBufferToBase64(buffer: ArrayBuffer): Promise<string> {
  let binary = ""
  const bytes = new Uint8Array(buffer)
  const chunkSize = 0x8000
  for (let index = 0; index < bytes.length; index += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(index, index + chunkSize))
  }
  return btoa(binary)
}

/**
 * Builds a file upload entry from a File object.
 */
export async function fileToUploadEntry(file: File, relativePath: string) {
  const buffer = await file.arrayBuffer()
  return {
    path: relativePath,
    content_base64: await arrayBufferToBase64(buffer),
    content_type: file.type || undefined,
  }
}

/**
 * Recursively extracts files from drag/drop file-system entries.
 */
export async function readEntriesRecursively(
  entry: FileSystemEntry,
  parentPath = ""
): Promise<Array<{ file: File; path: string }>> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry
    const file = await new Promise<File>((resolve, reject) => {
      fileEntry.file(resolve, reject)
    })
    return [{ file, path: `${parentPath}${file.name}` }]
  }

  const directoryEntry = entry as FileSystemDirectoryEntry
  const reader = directoryEntry.createReader()
  const children: FileSystemEntry[] = []

  while (true) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => {
      reader.readEntries(resolve, reject)
    })
    if (batch.length === 0) {
      break
    }
    children.push(...batch)
  }

  const nested = await Promise.all(
    children.map((child) =>
      readEntriesRecursively(child, `${parentPath}${directoryEntry.name}/`)
    )
  )
  return nested.flat()
}

/**
 * Converts a drag/drop event into upload-ready file entries.
 */
export async function extractDroppedFiles(
  event: DragEvent<HTMLDivElement>
): Promise<Array<{ file: File; path: string }>> {
  const itemEntries = Array.from(event.dataTransfer.items)
    .map((item) => item.webkitGetAsEntry?.())
    .filter(
      (entry): entry is FileSystemEntry => entry !== null && entry !== undefined
    )

  if (itemEntries.length > 0) {
    const nested = await Promise.all(
      itemEntries.map((entry) => readEntriesRecursively(entry))
    )
    return nested.flat()
  }

  return Array.from(event.dataTransfer.files).map((file) => ({
    file,
    path: file.webkitRelativePath || file.name,
  }))
}

/**
 * Computes SHA-256 digest for a file.
 */
export async function computeFileSha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer())
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("")
}

/**
 * Uploads a file to the given presigned session URL.
 */
export async function uploadFileToSession(
  file: File,
  uploadUrl: string,
  method: string,
  headers: Record<string, string>
): Promise<void> {
  const response = await fetch(uploadUrl, {
    method,
    headers,
    body: file,
  })
  if (!response.ok) {
    throw new Error(`Upload failed with ${response.status}`)
  }
}

/**
 * Merges server files with local draft changes for display.
 */
export function buildVisibleFiles(
  baseFiles:
    | Array<{
        path: string
        content_type: string
        size_bytes: number
      }>
    | undefined,
  draftChanges: Record<string, DraftChange>
): VisibleFileEntry[] {
  const entries = new Map<string, VisibleFileEntry>()
  for (const file of baseFiles ?? []) {
    entries.set(file.path, {
      path: file.path,
      contentType: file.content_type,
      sizeBytes: file.size_bytes,
      change: null,
      isNew: false,
    })
  }

  for (const [path, change] of Object.entries(draftChanges)) {
    if (change.kind === "delete") {
      const existing = entries.get(path)
      if (existing) {
        existing.change = change
      }
      continue
    }

    if (change.kind === "text") {
      const isNew = !entries.has(path)
      const sizeBytes = new TextEncoder().encode(change.content).length
      entries.set(path, {
        path,
        contentType: change.contentType,
        sizeBytes,
        change,
        isNew,
      })
      continue
    }

    const isNew = !entries.has(path)
    entries.set(path, {
      path,
      contentType: change.contentType,
      sizeBytes: change.file.size,
      change,
      isNew,
    })
  }

  return Array.from(entries.values()).sort((left, right) =>
    comparePaths(left.path, right.path)
  )
}

/**
 * Formats a skill version label.
 */
export function describeVersion(version: {
  version: number
  file_count: number
}): string {
  return `v${version.version} · ${version.file_count} file${
    version.file_count === 1 ? "" : "s"
  }`
}

/**
 * Builds a hierarchical file tree from the visible skill files.
 */
export function buildSkillFileTree(
  files: VisibleFileEntry[]
): SkillFileTreeNode[] {
  const root: SkillFileTreeNode[] = []

  for (const file of files) {
    const segments = file.path.split("/").filter(Boolean)
    let currentLevel = root
    let currentPath = ""

    for (const [index, segment] of segments.entries()) {
      currentPath = currentPath ? `${currentPath}/${segment}` : segment
      const isLeaf = index === segments.length - 1
      const existing = currentLevel.find((node) => node.name === segment)

      if (isLeaf) {
        const fileNode: SkillFileTreeNode = {
          kind: "file",
          name: segment,
          path: file.path,
          file,
        }
        if (existing) {
          const existingIndex = currentLevel.indexOf(existing)
          currentLevel.splice(existingIndex, 1, fileNode)
        } else {
          currentLevel.push(fileNode)
        }
        continue
      }

      if (existing?.kind === "folder") {
        currentLevel = existing.children
        continue
      }

      const folderNode: SkillFileTreeNode = {
        kind: "folder",
        name: segment,
        path: currentPath,
        children: [],
      }
      currentLevel.push(folderNode)
      currentLevel = folderNode.children
    }
  }

  return sortTreeChildren(root)
}

function sortTreeChildren(nodes: SkillFileTreeNode[]): SkillFileTreeNode[] {
  return nodes
    .map((node) => {
      if (node.kind === "folder") {
        return {
          ...node,
          children: sortTreeChildren(node.children),
        }
      }
      return node
    })
    .sort((left, right) => {
      if (left.kind !== right.kind) {
        return left.kind === "folder" ? -1 : 1
      }
      return comparePaths(left.name, right.name)
    })
}

/**
 * Returns all ancestor folder paths for a file path.
 */
export function getAncestorFolderPaths(path: string): string[] {
  const segments = path.split("/").filter(Boolean)
  return segments.slice(0, -1).map((_, index) => {
    return segments.slice(0, index + 1).join("/")
  })
}

/**
 * Renders version publishing status as a badge.
 */
export function renderValidationState(
  isPublishable: boolean | undefined,
  errorCount: number
): ReactNode {
  if (isPublishable) {
    return (
      <Badge variant="outline" className="gap-1">
        <CheckCircle2 className="size-3.5" />
        Ready to publish
      </Badge>
    )
  }

  return (
    <Badge variant="secondary" className="gap-1">
      <AlertCircle className="size-3.5" />
      {errorCount} publish blocker{errorCount === 1 ? "" : "s"}
    </Badge>
  )
}

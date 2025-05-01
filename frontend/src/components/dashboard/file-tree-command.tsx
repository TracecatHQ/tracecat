"use client"

import * as React from "react"
import { WorkflowFolderRead } from "@/client"
import { ChevronRight, Folder, FolderOpen } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandList,
} from "@/components/ui/command"

export type FileTreeItem = {
  name: string
  path: string
  children?: FileTreeItem[]
}

interface FileTreeCommandProps {
  items: FileTreeItem[]
  onSelect?: (path: string) => void
}

export function FileTreeCommand({ items, onSelect }: FileTreeCommandProps) {
  const [expandedFolders, setExpandedFolders] = React.useState<Set<string>>(
    new Set(["/"])
  )
  const [selectedItem, setSelectedItem] = React.useState<string | null>(null)

  const toggleFolder = (path: string) => {
    const newExpanded = new Set(expandedFolders)
    if (newExpanded.has(path)) {
      newExpanded.delete(path)
    } else {
      newExpanded.add(path)
    }
    setExpandedFolders(newExpanded)
  }

  const renderTreeItems = (items: FileTreeItem[], level = 0) => {
    return items.map((item) => {
      const isExpanded = expandedFolders.has(item.path)
      const isSelected = selectedItem === item.path
      const hasChildren = item.children && item.children.length > 0

      return (
        <React.Fragment key={item.path}>
          <CommandItem
            value={item.path}
            onSelect={() => {
              setSelectedItem(item.path)
              if (onSelect) {
                onSelect(item.path)
              }
              if (hasChildren) {
                toggleFolder(item.path)
              }
            }}
            className={cn(
              "flex items-center gap-2 px-2",
              { "pl-[calc(0.5rem+var(--indent))]": level > 0 },
              isSelected && "bg-accent"
            )}
            style={{ "--indent": `${level * 1.5}rem` } as React.CSSProperties}
          >
            <div className="flex items-center gap-2">
              {hasChildren ? (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-4 p-0"
                  onClick={(e) => {
                    e.stopPropagation()
                    toggleFolder(item.path)
                  }}
                >
                  <ChevronRight
                    className={cn(
                      "size-4 transition-transform",
                      isExpanded && "rotate-90"
                    )}
                  />
                </Button>
              ) : (
                <div className="w-4" /* Spacer for alignment */ />
              )}
              {isExpanded ? (
                <FolderOpen className="size-4 text-muted-foreground" />
              ) : (
                <Folder className="size-4 text-muted-foreground" />
              )}
              <span>{item.name}</span>
            </div>
          </CommandItem>

          {item.children &&
            isExpanded &&
            renderTreeItems(item.children, level + 1)}
        </React.Fragment>
      )
    })
  }

  return (
    <Command className="rounded-lg border shadow-md">
      <CommandList>
        <CommandEmpty>No results found.</CommandEmpty>
        <CommandGroup heading="Files and Folders">
          {renderTreeItems(items)}
        </CommandGroup>
      </CommandList>
    </Command>
  )
}

/**
 * Converts a list of folder paths into a hierarchical tree structure
 * @param paths List of folder paths (e.g. ["/folder1", "/folder1/folder2"])
 * @returns Root node of the tree structure
 */
export function buildFolderTree(paths: string[]): FileTreeItem[] {
  const root: FileTreeItem[] = []

  // Sort paths to ensure parent folders are processed first
  const sortedPaths = [...paths].sort()

  for (const path of sortedPaths) {
    // Split path into segments and remove empty strings
    const segments = path.split("/").filter(Boolean)

    let currentLevel = root
    let currentPath = ""

    for (const segment of segments) {
      // Update current path
      currentPath =
        currentPath === "" ? `/${segment}` : `${currentPath}/${segment}`

      // Find existing node at current level
      let node = currentLevel.find((n) => n.name === segment)

      if (!node) {
        // Create new node if it doesn't exist
        node = {
          name: segment,
          path: currentPath,
        }
        currentLevel.push(node)
      }

      // Initialize children array if needed
      if (!node.children) {
        node.children = []
      }

      // Move to next level
      currentLevel = node.children
    }
  }

  return root
}

export const ROOT_FOLDER_NAME = "Root (/)"
/**
 * Converts a list of folder paths into a hierarchical tree structure
 * @param folders List of folder paths (e.g. ["/folder1", "/folder1/folder2"])
 * @returns Root node of the tree structure
 */
export function getFileTreeItems(
  folders?: WorkflowFolderRead[]
): FileTreeItem[] {
  // Start with root folder
  const rootItem: FileTreeItem = {
    name: ROOT_FOLDER_NAME,
    path: "/",
    children: [],
  }

  const folderPaths = folders?.map((folder) => folder.path) || []

  // Get the folder tree structure
  const folderItems = buildFolderTree(folderPaths)

  // Add the folder tree as children of the root
  if (folderItems.length > 0) {
    rootItem.children = folderItems
  }

  return [rootItem]
}

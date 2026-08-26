/**
 * Generic, read-only file tree construction.
 *
 * This deliberately duplicates the ~40 lines of `buildSkillFileTree` /
 * `sortTreeChildren` in `src/lib/skills-studio.tsx`. Do not "fix" it by
 * extracting the original: `buildSkillFileTree` is coupled to
 * `VisibleFileEntry`'s draft-only `change` / `isNew` fields and to the hardcoded
 * `SKILL.md` pin, none of which a read-only version-diff tree has. Unifying them
 * would mean editing a working editor for no user-visible gain.
 */

/** Minimum shape a file must have to be placed in the tree. */
export interface FileTreeItem {
  /** Repository-relative path, e.g. `scripts/run.py`. */
  path: string
}

/** A folder node holding further nodes. */
export interface FileTreeFolderNode<TFile extends FileTreeItem> {
  kind: "folder"
  /** Last path segment. */
  name: string
  /** Full path of the folder, e.g. `scripts`. */
  path: string
  children: FileTreeNode<TFile>[]
}

/** A leaf node carrying the original file. */
export interface FileTreeFileNode<TFile extends FileTreeItem> {
  kind: "file"
  /** Last path segment. */
  name: string
  /** Full path of the file. */
  path: string
  /** The original file this node was built from. */
  file: TFile
}

/** A node in a file tree, discriminated on `kind`. */
export type FileTreeNode<TFile extends FileTreeItem> =
  | FileTreeFolderNode<TFile>
  | FileTreeFileNode<TFile>

/** Options for {@link buildFileTree}. */
export interface BuildFileTreeOptions {
  /**
   * Exact paths to sort first, in the order given, within their own level.
   * Used to pin well-known entrypoints such as `instructions.md`.
   */
  pinnedPaths?: readonly string[]
}

/**
 * Sorts a level and every level below it: pinned paths first in the order they
 * were given, then folders before files, then by path.
 */
function sortNodes<TFile extends FileTreeItem>(
  nodes: FileTreeNode<TFile>[],
  pinnedPaths: readonly string[]
): FileTreeNode<TFile>[] {
  return nodes
    .map((node) => {
      if (node.kind === "folder") {
        return { ...node, children: sortNodes(node.children, pinnedPaths) }
      }
      return node
    })
    .sort((left, right) => {
      const leftPin = pinnedPaths.indexOf(left.path)
      const rightPin = pinnedPaths.indexOf(right.path)
      if (leftPin !== -1 && rightPin !== -1) {
        return leftPin - rightPin
      }
      if (leftPin !== -1) {
        return -1
      }
      if (rightPin !== -1) {
        return 1
      }
      if (left.kind !== right.kind) {
        return left.kind === "folder" ? -1 : 1
      }
      return left.path.localeCompare(right.path)
    })
}

/**
 * Builds a nested folder/file tree from a flat list of files.
 *
 * Files keep their original object on the leaf node, so callers can attach
 * per-file state such as a diff status badge.
 */
export function buildFileTree<TFile extends FileTreeItem>(
  files: readonly TFile[],
  options: BuildFileTreeOptions = {}
): FileTreeNode<TFile>[] {
  const pinnedPaths = options.pinnedPaths ?? []
  const root: FileTreeNode<TFile>[] = []

  for (const file of files) {
    const segments = file.path.split("/").filter(Boolean)
    let currentLevel = root
    let currentPath = ""

    for (const [index, segment] of segments.entries()) {
      currentPath = currentPath ? `${currentPath}/${segment}` : segment
      const isLeaf = index === segments.length - 1
      const existing = currentLevel.find((node) => node.name === segment)

      if (isLeaf) {
        const fileNode: FileTreeFileNode<TFile> = {
          kind: "file",
          name: segment,
          path: file.path,
          file,
        }
        if (existing) {
          currentLevel.splice(currentLevel.indexOf(existing), 1, fileNode)
        } else {
          currentLevel.push(fileNode)
        }
        continue
      }

      if (existing?.kind === "folder") {
        currentLevel = existing.children
        continue
      }

      const folderNode: FileTreeFolderNode<TFile> = {
        kind: "folder",
        name: segment,
        path: currentPath,
        children: [],
      }
      currentLevel.push(folderNode)
      currentLevel = folderNode.children
    }
  }

  return sortNodes(root, pinnedPaths)
}

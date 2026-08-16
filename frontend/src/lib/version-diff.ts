import type {
  VersionFileEntry,
  VersionFileFingerprint,
  VersionFileStatus,
} from "@/components/version-history/types"

/**
 * Resolves the status of a single path from the two fingerprints.
 *
 * At least one side is always defined, because paths are collected from the
 * union of both manifests.
 */
function resolveFileStatus(
  draftFingerprint: string | undefined,
  versionFingerprint: string | undefined
): VersionFileStatus {
  if (draftFingerprint === undefined) {
    return "added"
  }
  if (versionFingerprint === undefined) {
    return "removed"
  }
  if (draftFingerprint === versionFingerprint) {
    return "unchanged"
  }
  return "modified"
}

/**
 * Compares a draft manifest against a version manifest and labels each path
 * with what restoring the version would do to the draft.
 *
 * `added` means the version has the path and the draft does not, so restoring
 * would add it back; `removed` means the draft has it and the version does not.
 * Equal fingerprints yield `unchanged`, so no file bodies are ever fetched to
 * build the tree. The result is sorted by path.
 */
export function compareVersionManifests(
  draftFiles: readonly VersionFileFingerprint[],
  versionFiles: readonly VersionFileFingerprint[]
): VersionFileEntry[] {
  const draftByPath = new Map<string, string>()
  for (const file of draftFiles) {
    draftByPath.set(file.path, file.fingerprint)
  }

  const versionByPath = new Map<string, string>()
  for (const file of versionFiles) {
    versionByPath.set(file.path, file.fingerprint)
  }

  const paths = new Set<string>([
    ...draftByPath.keys(),
    ...versionByPath.keys(),
  ])

  return Array.from(paths, (path) => ({
    path,
    status: resolveFileStatus(draftByPath.get(path), versionByPath.get(path)),
  })).sort((left, right) => left.path.localeCompare(right.path))
}

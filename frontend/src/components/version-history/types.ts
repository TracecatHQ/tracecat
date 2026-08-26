/**
 * Shared, document-agnostic types for the version history shell.
 *
 * The shell renders a version dropdown and a diff dialog for any document that
 * has immutable versions. Nothing here may reference skills, agent presets, or
 * any other concrete document kind — per-document knowledge lives in adapters.
 *
 * This module is intentionally free of JSX and React runtime imports so it can
 * be consumed by pure helpers in `src/lib/` without pulling React in.
 */

/**
 * One selectable row in the version history dropdown.
 */
export interface VersionHistoryEntry {
  /** Stable version identifier, used as the selection key. */
  id: string
  /** Primary display label, e.g. `v3` or `v3 · 4 files`. */
  label: string
  /**
   * Optional secondary label rendered beneath {@link label}, e.g. a relative
   * timestamp or an author name.
   */
  description?: string
  /** ISO 8601 creation timestamp of the version. */
  createdAt: string
  /**
   * True when this entry is current within its host-defined version stream.
   * When provided, this takes precedence over the document descriptor's
   * `currentVersionId` fallback, allowing hosts to mark multiple current
   * entries (for example, one per independently versioned field).
   */
  isCurrent?: boolean
}

/**
 * What restoring the selected version would do to the current draft.
 *
 * The perspective is deliberate and easy to invert, so read carefully:
 *
 * - `added` — the selected version has the file and the draft does not, so
 *   restoring would add it back.
 * - `removed` — the draft has the file and the selected version does not, so
 *   restoring would remove it from the draft.
 * - `modified` — both sides have the file with differing content.
 * - `unchanged` — both sides have the file with identical content.
 */
export type VersionFileStatus = "added" | "removed" | "modified" | "unchanged"

/**
 * A single file in the version diff file tree.
 */
export interface VersionFileEntry {
  /** Repository-relative path, e.g. `config.yaml` or `scripts/run.py`. */
  path: string
  /** What restoring the selected version would do to this path. */
  status: VersionFileStatus
}

/**
 * A path paired with a content fingerprint, used to diff two manifests without
 * fetching file bodies.
 *
 * Skills pass the server-provided `sha256`; agent presets pass the serialized
 * virtual-file text, which they already compute locally. Both sides of a
 * comparison must use the same fingerprinting scheme.
 */
export interface VersionFileFingerprint {
  /** Repository-relative path. */
  path: string
  /** Opaque content fingerprint. Equal fingerprints mean equal content. */
  fingerprint: string
}

/**
 * Describes the document the version history shell is operating on.
 *
 * Kept minimal and document-agnostic on purpose: the shell only needs enough to
 * write its copy and to mark the current version in the dropdown.
 */
export interface VersionedDocumentDescriptor {
  /** Human-readable kind of document, e.g. `agent` or `skill`. */
  entityLabel: string
  /** Display name of this particular document. */
  name: string
  /**
   * Current version fallback for entries without an explicit `isCurrent`.
   * Null when there is no single current version, including hosts with
   * multiple independently versioned streams.
   */
  currentVersionId: string | null
}

/**
 * The per-file diff payload the dialog body renders.
 *
 * Direction matches {@link VersionFileStatus}: `oldValue` is the current draft
 * and `newValue` is the selected version, so the diff reads as "what restoring
 * would change".
 */
export interface VersionFileDiffState {
  /** Path currently selected in the file tree. */
  path: string
  /** Current draft content. Null when the path is absent from the draft. */
  oldValue: string | null
  /** Selected version content. Null when absent from that version. */
  newValue: string | null
  /** MIME type hint used to pick the diff renderer. */
  contentType?: string
  /**
   * How to present the file. `text` renders an inline diff; `download` means
   * the content is not previewable and the dialog offers a download instead.
   * Defaults to `text` when omitted.
   */
  variant?: "text" | "download"
  /** Download target used when {@link variant} is `download`. */
  downloadUrl?: string | null
}

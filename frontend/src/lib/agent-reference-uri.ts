/** Stable reference identities. This parser never resolves or fetches a URI. */
export type ReferenceKind =
  | "tool"
  | "mcp-server"
  | "mcp-tool"
  | "table"
  | "workflow"
  | "skill"
  | "agent"

/** URI-only value, not a duplicate of an API resource or preparation payload. */
export interface ReferenceTarget {
  kind: ReferenceKind
  identity: string
  toolName?: string
}

/** Stable diagnostics shared with the Python URI parser. */
export type ReferenceURIErrorCode =
  | "invalid_uri"
  | "unsupported_version"
  | "invalid_identity"

/** A malformed reserved URI must not be treated as an ordinary external link. */
export class ReferenceURIError extends Error {
  constructor(public readonly code: ReferenceURIErrorCode) {
    super(code)
    this.name = "ReferenceURIError"
  }
}

const KINDS = new Set<string>([
  "tool",
  "mcp-server",
  "mcp-tool",
  "table",
  "workflow",
  "skill",
  "agent",
])
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/

/** Recognize the reserved scheme even if its spelling or version is invalid. */
export function isReferenceURI(value: string): boolean {
  return value.toLowerCase().startsWith("tracecat-ref:")
}

function validateTarget(target: ReferenceTarget): void {
  const { kind, identity, toolName } = target
  if (!KINDS.has(kind)) throw new ReferenceURIError("invalid_uri")
  if (kind === "tool") {
    if (
      identity.length > 255 ||
      identity.startsWith("mcp.") ||
      !/^[a-z0-9_]+(?:\.[a-z0-9_]+)+$/.test(identity)
    ) {
      throw new ReferenceURIError("invalid_identity")
    }
  } else if (!UUID_PATTERN.test(identity)) {
    throw new ReferenceURIError("invalid_identity")
  }
  if (kind === "mcp-tool") {
    if (!toolName || !/^[A-Za-z0-9_-]+$/.test(toolName))
      throw new ReferenceURIError("invalid_identity")
  } else if (toolName !== undefined) {
    throw new ReferenceURIError("invalid_identity")
  }
}

/** Decode exactly once without URL authority/path normalization. */
export function parseReferenceURI(value: string): ReferenceTarget {
  if (
    !value.startsWith("tracecat-ref://") ||
    /[\s\x00-\x1f\x7f?#\\]/.test(value)
  ) {
    throw new ReferenceURIError("invalid_uri")
  }
  const parts = value.slice("tracecat-ref://".length).split("/")
  if (parts[0] !== "v1") throw new ReferenceURIError("unsupported_version")
  if (![3, 4].includes(parts.length) || !KINDS.has(parts[1]))
    throw new ReferenceURIError("invalid_uri")
  const kind = parts[1] as ReferenceKind
  if (parts.length !== (kind === "mcp-tool" ? 4 : 3))
    throw new ReferenceURIError("invalid_uri")
  const decoded = parts.slice(2).map((segment) => {
    let item: string
    try {
      item = decodeURIComponent(segment)
    } catch {
      throw new ReferenceURIError("invalid_uri")
    }
    if (!item || /[/\\%?#\x00-\x1f\x7f]/.test(item))
      throw new ReferenceURIError("invalid_uri")
    return item
  })
  const target: ReferenceTarget = { kind, identity: decoded[0] }
  if (kind === "mcp-tool") target.toolName = decoded[1]
  validateTarget(target)
  return target
}

/** Serialize a validated identity; labels and published versions are excluded. */
export function serializeReferenceURI(target: ReferenceTarget): string {
  validateTarget(target)
  const segments = [target.identity]
  if (target.toolName !== undefined) segments.push(target.toolName)
  return `tracecat-ref://v1/${target.kind}/${segments.map(encodeURIComponent).join("/")}`
}

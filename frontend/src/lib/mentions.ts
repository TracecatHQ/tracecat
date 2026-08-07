/**
 * Inline mentions are encoded in markdown as ordinary links with a
 * `mention://` href, e.g. `[@Triage](mention://agent/<preset uuid>)`.
 *
 * The link label is authored text and is never trusted to identify the target;
 * only the id in the href is. Renderers resolve the display name from the id.
 */

/**
 * A well-formed `mention://` target.
 *
 * Discriminated on `type` so a future `mention://user/<uuid>` is one extra
 * variant here and one extra `case` at every call site.
 */
export type MentionTarget = { type: "agent"; presetId: string }

const MENTION_SCHEME = "mention://"

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Parse a `mention://` href into a typed target.
 *
 * Returns `null` for anything that is not exactly `mention://<segment>/<uuid>`
 * with a known segment, so callers can fall back to rendering a plain link.
 * Never throws.
 */
export function parseMentionHref(
  href: string | null | undefined
): MentionTarget | null {
  if (!href || !href.startsWith(MENTION_SCHEME)) {
    return null
  }

  const segments = href.slice(MENTION_SCHEME.length).split("/")
  if (segments.length !== 2) {
    return null
  }

  const [segment, id] = segments
  if (!id || !UUID_PATTERN.test(id)) {
    return null
  }

  switch (segment) {
    case "agent":
      return { type: "agent", presetId: id }
    default:
      return null
  }
}

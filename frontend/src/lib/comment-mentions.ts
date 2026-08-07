/**
 * Display-value mapping for comment mentions.
 *
 * The composer's textarea holds *display* text — a mention reads as `@Label` —
 * while the value sent to the API uses the wire token
 * `[@Label](mention://agent/<preset_id>)`. The bridge between the two is a list
 * of `MentionRange` offsets into the display text, maintained as the user edits
 * and converted by `serializeMentions` on submit.
 */

/** A mention occupying `[start, end)` of the display text. */
export interface MentionRange {
  start: number
  end: number
  label: string
  targetId: string
}

/** A single contiguous edit: `deleted` chars at `start` replaced by `inserted`. */
export interface TextSplice {
  start: number
  deleted: number
  inserted: number
}

/** Display text split into plain and mention runs, for the highlight overlay. */
export interface MentionSegment {
  start: number
  text: string
  mention: MentionRange | undefined
}

/** Display form of a mention, i.e. what the user sees in the textarea. */
export function mentionDisplayText(label: string): string {
  return `@${label}`
}

/**
 * Render a mention as its wire token.
 *
 * This shape is a shared contract with the comment renderer, so keep it
 * byte-for-byte stable.
 */
export function formatAgentMentionToken(mention: {
  label: string
  targetId: string
}): string {
  return `[@${mention.label}](mention://agent/${mention.targetId})`
}

function sortMentions(mentions: MentionRange[]): MentionRange[] {
  return [...mentions].sort((left, right) => left.start - right.start)
}

/** Convert display text plus mention ranges into the value sent to the API. */
export function serializeMentions(
  text: string,
  mentions: MentionRange[]
): string {
  let result = ""
  let cursor = 0
  for (const mention of sortMentions(mentions)) {
    if (mention.start < cursor || mention.end > text.length) {
      continue
    }
    result += text.slice(cursor, mention.start)
    result += formatAgentMentionToken(mention)
    cursor = mention.end
  }
  return result + text.slice(cursor)
}

/** Split display text into runs so the overlay can style mentions. */
export function buildMentionSegments(
  text: string,
  mentions: MentionRange[]
): MentionSegment[] {
  const segments: MentionSegment[] = []
  let cursor = 0
  for (const mention of sortMentions(mentions)) {
    if (mention.start < cursor || mention.end > text.length) {
      continue
    }
    if (mention.start > cursor) {
      segments.push({
        start: cursor,
        text: text.slice(cursor, mention.start),
        mention: undefined,
      })
    }
    segments.push({
      start: mention.start,
      text: text.slice(mention.start, mention.end),
      mention,
    })
    cursor = mention.end
  }
  if (cursor < text.length) {
    segments.push({
      start: cursor,
      text: text.slice(cursor),
      mention: undefined,
    })
  }
  return segments
}

/**
 * Derive the single contiguous edit between two values.
 *
 * The caret disambiguates otherwise-equivalent splices — typing `a` into `aa`
 * could be read as an edit at any of three offsets. Everything after the caret
 * is untouched by definition, so the common suffix is measured first and capped
 * there; the prefix then takes whatever is left before the caret. Measuring the
 * prefix first would bias the splice to the right and shift the wrong mentions.
 */
export function diffTextSplice(
  previous: string,
  next: string,
  caret: number
): TextSplice {
  const boundedCaret = Math.max(0, Math.min(caret, next.length))

  const maxSuffix = Math.min(
    previous.length,
    next.length,
    next.length - boundedCaret
  )
  let suffix = 0
  while (
    suffix < maxSuffix &&
    previous[previous.length - 1 - suffix] === next[next.length - 1 - suffix]
  ) {
    suffix++
  }

  const maxPrefix = Math.min(
    previous.length - suffix,
    next.length - suffix,
    boundedCaret
  )
  let prefix = 0
  while (prefix < maxPrefix && previous[prefix] === next[prefix]) {
    prefix++
  }

  return {
    start: prefix,
    deleted: previous.length - prefix - suffix,
    inserted: next.length - prefix - suffix,
  }
}

/**
 * Shift mention ranges across an edit.
 *
 * Edits before a mention move it, edits after leave it alone, and any edit that
 * reaches into a mention dissolves it — the characters stay in the text but
 * stop being a mention, which is what makes partial edits behave predictably.
 */
export function remapMentions(
  mentions: MentionRange[],
  splice: TextSplice
): MentionRange[] {
  const spliceEnd = splice.start + splice.deleted
  const delta = splice.inserted - splice.deleted
  const remapped: MentionRange[] = []
  for (const mention of mentions) {
    if (spliceEnd <= mention.start) {
      remapped.push({
        ...mention,
        start: mention.start + delta,
        end: mention.end + delta,
      })
      continue
    }
    if (splice.start >= mention.end) {
      remapped.push(mention)
    }
  }
  return remapped
}

/** Find the mention that ends exactly at `caret`, for atomic backspace. */
export function findMentionEndingAt(
  mentions: MentionRange[],
  caret: number
): MentionRange | undefined {
  return mentions.find((mention) => mention.end === caret)
}

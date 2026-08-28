/**
 * Display-value mapping for composer mentions.
 *
 * The composer's textarea holds *display* text — an agent mention reads as
 * `@Label` and a workflow command as `/Label` — while the value sent to the
 * API uses the wire token `[@Label](mention://agent/<preset_id>)` for agents
 * and no text at all for workflows, which travel as `workflow_id` instead. The
 * bridge between the two is a list of `MentionRange` offsets into the display
 * text, maintained as the user edits and converted by `serializeMentions` on
 * submit.
 */

/**
 * What a mention points at. Distinct from the generated `MentionTargetType`,
 * which is the backend wire enum and only knows `agent`.
 */
export type MentionKind = "agent" | "workflow"

/** A mention occupying `[start, end)` of the display text. */
export interface MentionRange {
  start: number
  end: number
  kind: MentionKind
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

/** The character that opens a mention session for each kind. */
const MENTION_TRIGGERS: Record<MentionKind, string> = {
  agent: "@",
  workflow: "/",
}

/** Display form of a mention, i.e. what the user sees in the textarea. */
export function mentionDisplayText(kind: MentionKind, label: string): string {
  return `${MENTION_TRIGGERS[kind]}${label}`
}

/**
 * Render an agent mention as its wire token.
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

/**
 * Convert display text plus mention ranges into the value sent to the API.
 *
 * Agent ranges become wire tokens. Workflow ranges are dropped along with one
 * following space, if present, so the surrounding text closes up cleanly; the
 * workflow itself is sent as `workflow_id`, not as text.
 */
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
    if (mention.kind === "agent") {
      result += formatAgentMentionToken(mention)
      cursor = mention.end
      continue
    }
    cursor = text[mention.end] === " " ? mention.end + 1 : mention.end
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

/** The single workflow command in the composer, if one has been picked. */
export function findWorkflowMention(
  mentions: MentionRange[]
): MentionRange | undefined {
  return mentions.find((mention) => mention.kind === "workflow")
}

/** The single agent mention in the composer, if one has been picked. */
export function findAgentMention(
  mentions: MentionRange[]
): MentionRange | undefined {
  return mentions.find((mention) => mention.kind === "agent")
}

/** Caret-anchored `@query` or `/query` span that drives the mention popover. */
export interface MentionToken {
  start: number
  end: number
  query: string
  kind: MentionKind
}

/**
 * Longest query a trigger will carry. Sized past the longest name the backend
 * accepts -- a preset name is capped at 120 characters -- so a target never
 * becomes unreachable by having its distinguishing suffix cut off. It is only
 * a backstop: a query that stops matching releases the trigger well before
 * this.
 */
const MAX_MENTION_QUERY_LENGTH = 160

function getTokenForKind(
  beforeCaret: string,
  caret: number,
  kind: MentionKind
): MentionToken | undefined {
  const triggerIndex = beforeCaret.lastIndexOf(MENTION_TRIGGERS[kind])
  if (triggerIndex < 0) {
    return undefined
  }

  const priorChar =
    triggerIndex === 0 ? " " : (beforeCaret[triggerIndex - 1] ?? " ")
  if (priorChar.trim() !== "") {
    return undefined
  }

  const query = beforeCaret.slice(triggerIndex + 1)
  // Agent and workflow names are usually several words, so a space has to be
  // able to sit inside a query. A leading space means the trigger was typed on
  // its own and prose followed, a newline always ends the mention, and the cap
  // stops a stray trigger from trailing the rest of a paragraph. The caller
  // closes the session once a multi-word query stops matching anything.
  if (/^\s/.test(query) || query.includes("\n")) {
    return undefined
  }
  if (query.length > MAX_MENTION_QUERY_LENGTH) {
    return undefined
  }

  return {
    start: triggerIndex,
    end: caret,
    query,
    kind,
  }
}

/**
 * Locate the `@query` or `/query` token immediately before the caret.
 *
 * The trigger must sit at the start of the text or directly after whitespace,
 * which is what keeps `/` inside URLs and paths from opening the popover. A
 * query may contain spaces so multi-word names can be typed out; it ends at a
 * newline, at a leading space, or at `MAX_MENTION_QUERY_LENGTH`. When both
 * triggers qualify, the one nearer the caret wins, so `@bar/baz` is still an
 * agent token.
 */
export function getMentionToken(
  text: string,
  caret: number
): MentionToken | undefined {
  const beforeCaret = text.slice(0, caret)
  const agent = getTokenForKind(beforeCaret, caret, "agent")
  const workflow = getTokenForKind(beforeCaret, caret, "workflow")
  if (agent && workflow) {
    return agent.start > workflow.start ? agent : workflow
  }
  return agent ?? workflow
}

/** Result of a pure text-plus-ranges edit, with the caret's landing offset. */
export interface MentionEdit {
  text: string
  mentions: MentionRange[]
  caret: number
}

/**
 * Replace the trigger token with a mention's display text plus a trailing
 * space, registering the new mention range.
 *
 * Pass `replaceExisting` on a surface that holds at most one mention of this
 * kind -- a comment runs one workflow, a chat session owns one preset -- so
 * picking a second target removes the first instead of leaving a stale name in
 * the text that quietly loses at submit.
 */
export function applyMentionInsertion(
  text: string,
  mentions: MentionRange[],
  token: MentionToken,
  mention: { kind: MentionKind; label: string; targetId: string },
  replaceExisting = false
): MentionEdit {
  let baseText = text
  let baseMentions = mentions
  let baseToken = token
  if (replaceExisting) {
    const existing = mentions.find((range) => range.kind === mention.kind)
    if (existing) {
      const removal = applyMentionRemoval(text, mentions, existing)
      baseText = removal.text
      baseMentions = removal.mentions
      if (existing.end <= token.start) {
        const removed = existing.end - existing.start
        baseToken = {
          ...token,
          start: token.start - removed,
          end: token.end - removed,
        }
      } else if (existing.start <= token.start) {
        // The caret was inside the old command, so the token text went with
        // it; insert where the old command began.
        baseToken = { ...token, start: existing.start, end: existing.start }
      }
    }
  }

  const display = mentionDisplayText(mention.kind, mention.label)
  const inserted = `${display} `
  return {
    text:
      baseText.slice(0, baseToken.start) +
      inserted +
      baseText.slice(baseToken.end),
    mentions: [
      ...remapMentions(baseMentions, {
        start: baseToken.start,
        deleted: baseToken.end - baseToken.start,
        inserted: inserted.length,
      }),
      {
        start: baseToken.start,
        end: baseToken.start + display.length,
        kind: mention.kind,
        label: mention.label,
        targetId: mention.targetId,
      },
    ],
    caret: baseToken.start + inserted.length,
  }
}

/** Remove `mention` and its text entirely, for atomic backspace. */
export function applyMentionRemoval(
  text: string,
  mentions: MentionRange[],
  mention: MentionRange
): MentionEdit {
  return {
    text: text.slice(0, mention.start) + text.slice(mention.end),
    mentions: remapMentions(mentions, {
      start: mention.start,
      deleted: mention.end - mention.start,
      inserted: 0,
    }),
    caret: mention.start,
  }
}

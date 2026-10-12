import {
  isMap,
  isScalar,
  isSeq,
  LineCounter,
  type ParsedNode,
  parseDocument,
  stringify,
} from "yaml"
import type {
  FormConfig_Input,
  FormConfig_Output,
  FormIssueSeverity,
} from "@/client"

/** A problem in the config text, placed on the characters that caused it. */
export interface FormConfigProblem {
  severity: FormIssueSeverity
  message: string
  /** Character offsets into the text the problem was found in. */
  from: number
  to: number
  /** One-based line of `from`. */
  line: number
}

/** A problem the server reports, placed by a path into the config. */
export interface FormConfigIssue {
  severity: FormIssueSeverity
  message: string
  /** Path to the offending config key, e.g. `["layout", 2, "widget"]`. */
  loc: ReadonlyArray<string | number>
}

/** Result of {@link parseFormConfigYaml}. */
export interface ParsedFormConfig {
  /** The config as JSON, or `null` when the text cannot be read as one. */
  config: FormConfig_Input | null
  /** YAML problems. Empty when `config` is set. */
  problems: FormConfigProblem[]
}

const NOT_A_MAPPING_MESSAGE =
  "The config must be a mapping with keys such as title and layout."

function lineEnd(text: string, from: number): number {
  const index = text.indexOf("\n", from)
  return index === -1 ? text.length : index
}

/**
 * Read the config editor's YAML as the JSON the API takes.
 *
 * Empty text is an empty config. Syntax errors come back as problems with
 * the position the parser reports, and `config` is then `null`.
 */
export function parseFormConfigYaml(text: string): ParsedFormConfig {
  const lineCounter = new LineCounter()
  const document = parseDocument(text, { lineCounter })
  if (document.errors.length > 0) {
    return {
      config: null,
      problems: document.errors.map((error) => {
        const from = Math.min(error.pos[0], text.length)
        return {
          severity: "error",
          // The parser appends a code frame after the first line.
          message: error.message.split("\n")[0],
          from,
          to: Math.max(from, Math.min(error.pos[1], lineEnd(text, from))),
          line: lineCounter.linePos(from).line,
        }
      }),
    }
  }
  const value: unknown = document.toJS()
  if (value === null || value === undefined) {
    return { config: {}, problems: [] }
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    return {
      config: null,
      problems: [
        {
          severity: "error",
          message: NOT_A_MAPPING_MESSAGE,
          from: 0,
          to: lineEnd(text, 0),
          line: 1,
        },
      ],
    }
  }
  return { config: value as FormConfig_Input, problems: [] }
}

interface Located {
  from: number
  to: number
}

function childOf(
  node: ParsedNode,
  key: string | number
): { node: ParsedNode | null; from: number } | null {
  if (isMap(node)) {
    const pair = node.items.find(
      (item) => isScalar(item.key) && String(item.key.value) === String(key)
    )
    if (!pair) {
      return null
    }
    // Start at the key, so `widget: cards` is marked from `widget`.
    return {
      node: pair.value ?? null,
      from: (pair.key as ParsedNode).range[0],
    }
  }
  if (isSeq(node) && typeof key === "number") {
    const item = node.items[key]
    return item ? { node: item, from: item.range[0] } : null
  }
  return null
}

function locate(
  root: ParsedNode | null,
  loc: ReadonlyArray<string | number>
): Located {
  let node = root
  let located: Located = { from: 0, to: root ? root.range[1] : 0 }
  for (let index = 0; index < loc.length && node; index++) {
    const child = childOf(node, loc[index])
    if (!child) {
      // Not in the text: the path has a segment the YAML does not, such as
      // the union tag Pydantic adds. Skip it and keep the deepest match.
      continue
    }
    // `field` is both a real key and the tag of a field entry. When the
    // next segment is a sibling key, this one was the tag.
    const next = loc[index + 1]
    const isTag =
      next !== undefined &&
      (child.node === null || isScalar(child.node)) &&
      childOf(node, next) !== null
    if (isTag) {
      continue
    }
    located = {
      from: child.from,
      to: child.node ? child.node.range[1] : child.from,
    }
    node = child.node
  }
  return located
}

/**
 * Place issues the server reports by config path onto the YAML text.
 *
 * Each issue is marked on the first line of the deepest part of its path
 * that the text has, so an issue about a key the author left out lands on
 * its parent. Issues are returned errors first, then by position.
 */
export function locateFormIssues(
  text: string,
  issues: readonly FormConfigIssue[]
): FormConfigProblem[] {
  if (issues.length === 0) {
    return []
  }
  const lineCounter = new LineCounter()
  const document = parseDocument(text, { lineCounter })
  const problems = issues.map((issue) => {
    const { from, to } = locate(document.contents, issue.loc)
    return {
      severity: issue.severity,
      message: issue.message,
      from,
      to: Math.max(from, Math.min(to, lineEnd(text, from))),
      line: lineCounter.linePos(from).line,
    }
  })
  return problems.sort(
    (a, b) =>
      Number(b.severity === "error") - Number(a.severity === "error") ||
      a.from - b.from
  )
}

/**
 * Write a stored config as the YAML the editor shows.
 *
 * The server stores JSON, so this is where comments and the author's own
 * formatting are lost. An empty config is empty text. Long values such as
 * inline images stay on one line.
 */
export function serializeFormConfig(config: FormConfig_Output): string {
  if (Object.keys(config).length === 0) {
    return ""
  }
  return stringify(config, { lineWidth: 0 })
}

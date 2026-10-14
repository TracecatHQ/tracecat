import type { Diagnostic } from "@codemirror/lint"
import {
  isMap,
  isScalar,
  isSeq,
  type ParsedNode,
  parseDocument,
  stringify,
} from "yaml"
import type {
  FormConfig_Input,
  FormConfig_Output,
  FormIssue,
  FormIssueSeverity,
} from "@/client"

/** A problem in the config text, placed on the characters that caused it. */
export type FormConfigProblem = Diagnostic & { severity: FormIssueSeverity }

/** A problem the server reports, placed by a path into the config. */
export type FormConfigIssue = Pick<FormIssue, "severity" | "message" | "loc">

function lineEnd(text: string, from: number): number {
  const index = text.indexOf("\n", from)
  return index === -1 ? text.length : index
}

/**
 * Read the config editor's YAML as the JSON the API takes. Empty text is an
 * empty config. Syntax errors come back as problems at the position the
 * parser reports, and `config` is then `null`.
 */
export function parseFormConfigYaml(text: string): {
  config: FormConfig_Input | null
  problems: FormConfigProblem[]
} {
  const document = parseDocument(text)
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
        }
      }),
    }
  }
  const value: unknown = document.toJS()
  if (value === null || value === undefined) {
    return { config: {}, problems: [] }
  }
  if (typeof value !== "object" || Array.isArray(value)) {
    const message =
      "The config must be a mapping with keys such as title and layout."
    return {
      config: null,
      problems: [{ severity: "error", message, from: 0, to: lineEnd(text, 0) }],
    }
  }
  return { config: value as FormConfig_Input, problems: [] }
}

function childOf(
  node: ParsedNode,
  key: string | number
): { node: ParsedNode | null; from: number } | null {
  if (isMap(node)) {
    const pair = node.items.find(
      (item) => isScalar(item.key) && String(item.key.value) === String(key)
    )
    // Start at the key, so `widget: cards` is marked from `widget`.
    return pair
      ? { node: pair.value ?? null, from: (pair.key as ParsedNode).range[0] }
      : null
  }
  if (isSeq(node) && typeof key === "number") {
    const item = node.items[key]
    return item ? { node: item, from: item.range[0] } : null
  }
  return null
}

/**
 * Place issues the server reports by config path onto the YAML text. Each
 * is marked on the first line of the deepest part of its path that the text
 * has, so an issue about a key the author left out lands on its parent.
 */
export function locateFormIssues(
  text: string,
  issues: readonly FormConfigIssue[]
): FormConfigProblem[] {
  const root = issues.length > 0 ? parseDocument(text).contents : null
  return issues.map(({ severity, message, loc }) => {
    let node = root
    let from = 0
    let to = root ? root.range[1] : 0
    for (let index = 0; index < loc.length && node; index++) {
      const child = childOf(node, loc[index])
      // A segment the text does not have, such as the union tag Pydantic
      // adds, is skipped. `field` is both a real key and the tag of a field
      // entry: when the next segment is a sibling key, this one was the tag.
      const next = loc[index + 1]
      const isTag =
        next !== undefined &&
        (child?.node === null || isScalar(child?.node)) &&
        childOf(node, next) !== null
      if (!child || isTag) {
        continue
      }
      from = child.from
      to = child.node ? child.node.range[1] : child.from
      node = child.node
    }
    return {
      severity,
      message,
      from,
      to: Math.max(from, Math.min(to, lineEnd(text, from))),
    }
  })
}

/**
 * Write a stored config as the YAML the editor shows. The server stores
 * JSON, so comments and formatting are lost here. An empty config is empty
 * text; long values such as inline images stay on one line.
 */
export function serializeFormConfig(config: FormConfig_Output): string {
  return Object.keys(config).length === 0
    ? ""
    : stringify(config, { lineWidth: 0 })
}

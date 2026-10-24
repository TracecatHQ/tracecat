import { type Document, isSeq, parseDocument } from "yaml"

/** A path into the form config, such as `["layout", 0, "label"]`. */
export type FormConfigPath = (string | number)[]

/** The kinds of layout block, each named by the key that holds its content. */
export const FORM_BLOCK_KINDS = [
  "field",
  "heading",
  "text",
  "notice",
  "divider",
] as const

/** A kind of layout block. */
export type FormBlockKind = (typeof FORM_BLOCK_KINDS)[number]

/** One layout entry as plain data. */
export type FormLayoutItem = Record<string, unknown>

/** The parts of a form config the visual editor shows. */
export interface FormVisualConfig {
  title: string
  description: string
  successMessage: string
  layout: FormLayoutItem[]
}

/** Whether `value` is a plain object, such as a layout entry. */
export function isFormLayoutItem(value: unknown): value is FormLayoutItem {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function asText(value: unknown): string {
  if (value === undefined || value === null) {
    return ""
  }
  return typeof value === "string" ? value : String(value)
}

function parse(text: string): Document | null {
  const document = parseDocument(text)
  return document.errors.length > 0 ? null : document
}

/**
 * Read the config text for the visual editor. Returns `null` when the text
 * has syntax errors or a shape the editor can't show, such as a `layout`
 * that is not a list of entries.
 */
export function readFormVisualConfig(text: string): FormVisualConfig | null {
  const document = parse(text)
  if (!document) {
    return null
  }
  let value: unknown
  try {
    value = document.toJS() ?? {}
  } catch {
    return null
  }
  if (!isFormLayoutItem(value)) {
    return null
  }
  const layout = value.layout ?? []
  if (!Array.isArray(layout) || !layout.every(isFormLayoutItem)) {
    return null
  }
  return {
    title: asText(value.title),
    description: asText(value.description),
    successMessage: asText(value.success_message),
    layout,
  }
}

/** The kind of a layout entry, or `null` when it names none. */
export function formBlockKind(item: FormLayoutItem): FormBlockKind | null {
  return FORM_BLOCK_KINDS.find((kind) => kind in item) ?? null
}

/**
 * Apply `change` to the parsed text and write it back. Comments and keys the
 * change doesn't touch are kept. Text that doesn't parse is returned as is.
 */
function rewrite(text: string, change: (document: Document) => void): string {
  const document = parse(text)
  if (!document) {
    return text
  }
  change(document)
  return document.toString({ lineWidth: 0 })
}

/**
 * Set the value at `path`. An empty string, `false`, `null` or `undefined`
 * removes the key instead, so defaults stay implicit, unless `keepEmpty` is
 * set for a key the entry can't go without.
 */
export function setFormConfigValue(
  text: string,
  path: FormConfigPath,
  value: unknown,
  { keepEmpty = false }: { keepEmpty?: boolean } = {}
): string {
  return rewrite(text, (document) => {
    const remove =
      value === undefined ||
      value === null ||
      value === false ||
      (value === "" && !keepEmpty)
    if (!remove) {
      document.setIn(path, value)
    } else if (document.hasIn(path)) {
      document.deleteIn(path)
    }
  })
}

/** Insert `value` into the list at `path`, creating the list if missing. */
export function insertFormConfigItem(
  text: string,
  path: FormConfigPath,
  value: unknown,
  index?: number
): string {
  return rewrite(text, (document) => {
    const list = document.getIn(path, true)
    if (isSeq(list)) {
      list.items.splice(
        index ?? list.items.length,
        0,
        document.createNode(value)
      )
    } else {
      document.setIn(path, document.createNode([value]))
    }
  })
}

/** Remove an item from the list at `path`, and the list too if `dropEmpty`. */
export function removeFormConfigItem(
  text: string,
  path: FormConfigPath,
  index: number,
  { dropEmpty = false }: { dropEmpty?: boolean } = {}
): string {
  return rewrite(text, (document) => {
    const list = document.getIn(path, true)
    if (!isSeq(list)) {
      return
    }
    list.items.splice(index, 1)
    if (dropEmpty && list.items.length === 0) {
      document.deleteIn(path)
    }
  })
}

/** Move an item, with its comments, within the list at `path`. */
export function moveFormConfigItem(
  text: string,
  path: FormConfigPath,
  from: number,
  to: number
): string {
  return rewrite(text, (document) => {
    const list = document.getIn(path, true)
    if (!isSeq(list) || from === to) {
      return
    }
    const [item] = list.items.splice(from, 1)
    list.items.splice(to, 0, item)
  })
}

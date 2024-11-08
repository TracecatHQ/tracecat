import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import YAML, { Schema } from "yaml"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
export const copyToClipboard = async ({
  target,
  message,
  value,
}: {
  target?: string
  value?: string
  message?: string
}) => {
  try {
    let copyValue = ""
    if (!navigator.clipboard) {
      throw new Error("Browser doesn't have support for native clipboard.")
    }
    if (target) {
      const node = document.querySelector(target)
      if (!node || !node.textContent) {
        throw new Error("Element not found")
      }
      value = node.textContent
    }
    if (value) {
      copyValue = value
    }
    await navigator.clipboard.writeText(copyValue)
    console.log(message ?? "Copied!!!")
  } catch (error) {
    console.log(error)
  }
}

export function slugify(value: string, delimiter: string = "_"): string {
  return value
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9 ]/g, "")
    .replace(/\s+/g, delimiter)
}

export function undoSlugify(value: string, delimiter: string = "_"): string {
  return value
    .replace(new RegExp(delimiter, "g"), " ")
    .replace(/\b\w/g, (l) => l.toUpperCase())
}

export function undoSlugifyNamespaced(
  value: string,
  namespaceDelimiter: string = ".",
  wordDelimiter: string = "_"
): string {
  return value
    .split(namespaceDelimiter)
    .map((v) => undoSlugify(v, wordDelimiter))
    .join(" ")
}
/**
 *
 * @param key <Action ID>.<Action Slug>
 * @returns <Action Slug>
 */
export function getSlugFromActionKey(key: string): string {
  return key.split(".")[1]
}

export function tryStringify(value: string, defaultValue: string = ""): string {
  try {
    return JSON.stringify(value, null, 2)
  } catch (error) {
    return defaultValue
  }
}

export function parseActionRunId(
  actionRunId: string,
  field: "actionId" | "actionSlug" | "workflowRunId" = "actionSlug"
): string {
  if (!actionRunId.startsWith("ar:")) {
    throw new Error("Invalid action run ID")
  }
  const [, actionKey, workflowRunId] = actionRunId.split(":")
  switch (field) {
    case "actionId":
      return actionKey.split(".")[0]
    case "actionSlug":
      return actionKey.split(".")[1]
    case "workflowRunId":
      return workflowRunId
    default:
      throw new Error("Invalid field")
  }
}

export const loadFromLocalStorage = <T>(key: string): T => {
  const storedValue = localStorage.getItem(key)
  return storedValue ? JSON.parse(storedValue) : []
}
export const storeInLocalStorage = <T>(key: string, value: T) => {
  localStorage.setItem(key, JSON.stringify(value))
}

export const deleteFromLocalStorage = (key: string) => {
  localStorage.removeItem(key)
}

export function groupBy<T, K extends keyof T>(
  array: T[],
  key: K
): Record<string, T[]> {
  return array.reduce(
    (accumulator, currentItem) => {
      const groupKey = currentItem[key] as unknown as string
      if (!accumulator[groupKey]) {
        accumulator[groupKey] = []
      }
      accumulator[groupKey].push(currentItem)
      return accumulator
    },
    {} as Record<string, T[]>
  )
}

export function isServer() {
  return typeof window === "undefined"
}

export function isEmptyObjectOrNullish(value: unknown) {
  return value === null || value === undefined || isEmptyObject(value)
}
export function isEmptyObject(obj: object) {
  return typeof obj === "object" && Object.keys(obj).length === 0
}

export function itemOrEmptyString(item: unknown | undefined) {
  return isEmptyObjectOrNullish(item)
    ? ""
    : YAML.stringify(item, {
        keepSourceTokens: true,
        strict: false,
        schema: new Schema({
          sortMapEntries: false,
        }),
      })
}

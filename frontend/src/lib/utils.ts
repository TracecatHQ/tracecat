import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"
import YAML from "yaml"

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
  return isEmptyObjectOrNullish(item) ? "" : YAML.stringify(item)
}

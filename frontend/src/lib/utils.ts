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

export function capitalizeFirst(str: string) {
  return str.charAt(0).toUpperCase() + str.slice(1)
}

export function splitConditionalExpression(
  s: string,
  maxLength: number = 30,
  operators: string[] = ["&&", "||", "=="]
): string {
  // 1) Tokenize on operators (keeping the delimiters)
  const operatorPattern = new RegExp(
    `(${operators.map((op) => op.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`
  )
  const parts = s
    .split(operatorPattern)
    .map((p) => p.trim())
    .filter((p) => p.length > 0)

  if (parts.length === 0) {
    return ""
  }

  // 2) Build lines greedily
  const lines: string[] = []
  let currentLine = parts[0] // start with the first operand

  for (let i = 1; i < parts.length; i += 2) {
    const op = parts[i]
    const operand = parts[i + 1] ?? ""
    const chunk = `${op} ${operand}`

    // If adding this chunk would exceed maxLength, emit the current line…
    if (currentLine.length + 1 + chunk.length > maxLength) {
      lines.push(currentLine)
      currentLine = chunk
    } else {
      // …otherwise, tack it on
      currentLine += " " + chunk
    }
  }

  // push whatever's left
  lines.push(currentLine)

  // 3) If we ended up with exactly one line and it's ≤ maxLength, return the original
  if (lines.length === 1 && lines[0].length <= maxLength) {
    return s
  }

  return lines.join("\n")
}

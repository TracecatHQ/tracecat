import { z } from "zod"
import type { VariableReadMinimal } from "@/client"

/** Matches the backend `VariableName` constraint: lowercase snake case. */
export const VARIABLE_NAME_PATTERN = /^[a-z0-9_]+$/

export const VARIABLE_NAME_ERROR =
  "Use lowercase letters, numbers, and underscores only (e.g. api_config)."

/** Zod schema for a variable name accepted by the backend. */
export const variableNameSchema = z
  .string()
  .trim()
  .min(1, "Name is required.")
  .max(255, "Name must be 255 characters or fewer.")
  .regex(VARIABLE_NAME_PATTERN, VARIABLE_NAME_ERROR)

export interface VariableGroup {
  name: string
  items: VariableReadMinimal[]
  environments: string[]
}

export function normalizeVariableEnvironment(
  environment: string | null | undefined
) {
  return environment?.trim() || "default"
}

export function buildVariableGroups(
  variables: VariableReadMinimal[]
): VariableGroup[] {
  const variablesByName = new Map<string, VariableReadMinimal[]>()

  for (const variable of variables) {
    const currentItems = variablesByName.get(variable.name) ?? []
    currentItems.push(variable)
    variablesByName.set(variable.name, currentItems)
  }

  return Array.from(variablesByName.entries())
    .map(([name, items]) => {
      const sortedItems = [...items].sort((a, b) =>
        normalizeVariableEnvironment(a.environment).localeCompare(
          normalizeVariableEnvironment(b.environment)
        )
      )

      return {
        name,
        items: sortedItems,
        environments: sortedItems.map((item) =>
          normalizeVariableEnvironment(item.environment)
        ),
      } satisfies VariableGroup
    })
    .sort((a, b) => a.name.localeCompare(b.name))
}

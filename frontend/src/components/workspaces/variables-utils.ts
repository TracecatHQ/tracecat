import type { VariableReadMinimal } from "@/client"

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

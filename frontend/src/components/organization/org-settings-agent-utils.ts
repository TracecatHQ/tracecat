import type { ModelCatalogEntry, ModelSelection } from "@/client"

function toModelSelection(
  model: Pick<ModelCatalogEntry, "source_id" | "model_provider" | "model_name">
): ModelSelection {
  return {
    source_id: model.source_id ?? null,
    model_provider: model.model_provider,
    model_name: model.model_name,
  }
}

function getModelSelectionKey(selection: {
  source_id?: string | null
  model_provider?: string | null
  model_name?: string | null
}): string {
  return `${selection.source_id ?? "platform"}::${selection.model_provider ?? ""}::${selection.model_name ?? ""}`
}

function hasSameModelSelection(
  left:
    | Pick<ModelSelection, "source_id" | "model_provider" | "model_name">
    | null
    | undefined,
  right:
    | Pick<ModelSelection, "source_id" | "model_provider" | "model_name">
    | null
    | undefined
): boolean {
  if (!left || !right) {
    return left == null && right == null
  }
  return getModelSelectionKey(left) === getModelSelectionKey(right)
}

export function mergeCustomSourceRows(
  cachedRows: ModelCatalogEntry[] | undefined,
  liveModels: ModelCatalogEntry[] | undefined,
  sourceId: string
): ModelCatalogEntry[] {
  const liveSourceModels =
    liveModels?.filter((model) => model.source_id === sourceId) ?? []
  if (!cachedRows?.length) {
    return liveSourceModels
  }

  const mergedRows = cachedRows.map((row) => {
    const liveRow = liveSourceModels.find((model) =>
      hasSameModelSelection(toModelSelection(model), toModelSelection(row))
    )
    return liveRow ?? row
  })
  const seenKeys = new Set(
    mergedRows.map((row) => getModelSelectionKey(toModelSelection(row)))
  )

  for (const liveRow of liveSourceModels) {
    const key = getModelSelectionKey(toModelSelection(liveRow))
    if (seenKeys.has(key)) {
      continue
    }
    mergedRows.push(liveRow)
    seenKeys.add(key)
  }

  return mergedRows
}

import type {
  CollectionObject,
  ExternalObject,
  InlineObject,
  ObjectRef,
} from "@/client"

type UnknownRecord = Record<string, unknown>

export type ExternalObjectRef = ObjectRef
export type ExternalStoredObject = ExternalObject
export type CollectionStoredObject = CollectionObject
export type StoredObject = InlineObject | ExternalObject | CollectionObject

function isObjectRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null
}

function isExternalObjectRef(value: unknown): value is ObjectRef {
  if (!isObjectRecord(value)) {
    return false
  }
  const hasRequiredFields =
    typeof value.bucket === "string" &&
    typeof value.key === "string" &&
    typeof value.size_bytes === "number" &&
    typeof value.sha256 === "string"

  if (!hasRequiredFields) {
    return false
  }

  if (value.backend !== undefined && value.backend !== "s3") {
    return false
  }

  if (
    value.content_type !== undefined &&
    typeof value.content_type !== "string"
  ) {
    return false
  }

  if (
    value.encoding !== undefined &&
    value.encoding !== "json" &&
    value.encoding !== "json+zstd" &&
    value.encoding !== "json+gzip"
  ) {
    return false
  }

  if (value.created_at !== undefined && typeof value.created_at !== "string") {
    return false
  }

  return true
}

export function isExternalStoredObject(
  value: unknown
): value is ExternalStoredObject {
  if (!isObjectRecord(value)) {
    return false
  }
  return value.type === "external" && isExternalObjectRef(value.ref)
}

export function isCollectionStoredObject(
  value: unknown
): value is CollectionStoredObject {
  if (!isObjectRecord(value)) {
    return false
  }
  return (
    value.type === "collection" &&
    isExternalObjectRef(value.manifest_ref) &&
    typeof value.count === "number" &&
    typeof value.chunk_size === "number" &&
    (value.element_kind === "value" || value.element_kind === "stored_object")
  )
}

export function isInlineStoredObject(value: unknown): value is InlineObject {
  if (!isObjectRecord(value)) {
    return false
  }
  return value.type === "inline" && "data" in value
}

export function isStoredObject(value: unknown): value is StoredObject {
  return (
    isInlineStoredObject(value) ||
    isExternalStoredObject(value) ||
    isCollectionStoredObject(value)
  )
}

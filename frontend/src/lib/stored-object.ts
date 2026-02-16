type UnknownRecord = Record<string, unknown>

import type { ExternalObject, ObjectRef } from "@/client"

export type ExternalObjectRef = ObjectRef
export type ExternalStoredObject = ExternalObject

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

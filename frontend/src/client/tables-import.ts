import type { CancelablePromise } from "./core/CancelablePromise"
import { OpenAPI } from "./core/OpenAPI"
import { request as __request } from "./core/request"
import type { TableRead } from "./types.gen"

export type TablesCreateTableFromCsvData = {
  formData: {
    name: string
    file: File
  }
  workspaceId: string
}

export type TablesCreateTableFromCsvResponse = TableRead

export const tablesCreateTableFromCsv = (
  data: TablesCreateTableFromCsvData
): CancelablePromise<TablesCreateTableFromCsvResponse> => {
  return __request(OpenAPI, {
    method: "POST",
    url: "/tables/import",
    query: {
      workspace_id: data.workspaceId,
    },
    formData: data.formData,
    mediaType: "multipart/form-data",
    errors: {
      422: "Validation Error",
    },
  })
}

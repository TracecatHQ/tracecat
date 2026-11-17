import { AxiosError, AxiosHeaders } from "axios"
import { handleExportError } from "@/lib/export"

function axiosError(status: number, data: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() }
  return new AxiosError(
    `Request failed with status code ${status}`,
    undefined,
    config,
    undefined,
    { status, statusText: "", headers: {}, config, data }
  )
}

describe("handleExportError", () => {
  it("shows the server's detail when it is a message", () => {
    const detail =
      "Cannot export draft: Action 'build_payload' has an invalid expression in field 'inputs'."
    expect(handleExportError(axiosError(400, { detail })).description).toBe(
      detail
    )
  })

  it("falls back to the generic message without a detail", () => {
    expect(handleExportError(axiosError(500, {})).description).toBe(
      "Could not export workflow. Please try again. Request failed with status code 500"
    )
  })
})

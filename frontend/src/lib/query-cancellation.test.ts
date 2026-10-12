import { waitFor } from "@testing-library/react"
import axios from "axios"
import { CancelablePromise, CancelError } from "@/client/core/CancelablePromise"
import { OpenAPI } from "@/client/core/OpenAPI"
import { request } from "@/client/core/request"
import { withAbortSignal } from "@/lib/query-cancellation"

describe("withAbortSignal", () => {
  it("cancels an already-aborted SDK request", async () => {
    const controller = new AbortController()
    controller.abort()
    const sdkRequest = new CancelablePromise<string>(() => undefined)
    await expect(
      withAbortSignal(controller.signal, sdkRequest)
    ).rejects.toBeInstanceOf(CancelError)
    expect(sdkRequest.isCancelled).toBe(true)
  })

  it("removes its abort listener after successful completion", async () => {
    const controller = new AbortController()
    const removeListener = jest.spyOn(controller.signal, "removeEventListener")
    const sdkRequest = new CancelablePromise<string>((resolve) =>
      resolve("synthetic result")
    )
    const cancel = jest.spyOn(sdkRequest, "cancel")
    await expect(withAbortSignal(controller.signal, sdkRequest)).resolves.toBe(
      "synthetic result"
    )
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function))
    controller.abort()
    expect(cancel).not.toHaveBeenCalled()
  })

  it("removes its listener after request failure", async () => {
    const controller = new AbortController()
    const removeListener = jest.spyOn(controller.signal, "removeEventListener")
    const sdkRequest = new CancelablePromise<string>((_resolve, reject) =>
      reject(new Error("Synthetic failure"))
    )
    await expect(
      withAbortSignal(controller.signal, sdkRequest)
    ).rejects.toThrow("Synthetic failure")
    expect(removeListener).toHaveBeenCalledWith("abort", expect.any(Function))
  })

  it("forwards query abort to the generated Axios transport's AbortSignal", async () => {
    const transport = jest
      .spyOn(axios, "request")
      .mockImplementation(() => new Promise(() => undefined))
    const controller = new AbortController()
    const sdkRequest = request(OpenAPI, {
      method: "GET",
      url: "/synthetic-case-search",
    })
    const response = withAbortSignal(controller.signal, sdkRequest)
    try {
      await waitFor(() => expect(transport).toHaveBeenCalledTimes(1))
      const signal = transport.mock.calls[0][0].signal
      expect(signal?.aborted).toBe(false)
      controller.abort()
      await expect(response).rejects.toBeInstanceOf(CancelError)
      expect(signal?.aborted).toBe(true)
    } finally {
      transport.mockRestore()
    }
  })
})

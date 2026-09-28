/**
 * @jest-environment jsdom
 */

import { copyToClipboard } from "@/lib/utils"

/** Swap `navigator.clipboard` per test; jsdom does not define it at all. */
function defineClipboard(clipboard: unknown) {
  Object.defineProperty(navigator, "clipboard", {
    value: clipboard,
    configurable: true,
  })
}

describe("copyToClipboard", () => {
  afterEach(() => {
    defineClipboard(undefined)
    jest.restoreAllMocks()
  })

  it("returns true after a successful write", async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    defineClipboard({ writeText })

    await expect(copyToClipboard({ value: "abc123" })).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith("abc123")
  })

  it("returns false when the clipboard API is absent", async () => {
    // The self-hosted plain-HTTP case: `navigator.clipboard` is undefined.
    defineClipboard(undefined)
    jest.spyOn(console, "log").mockImplementation(() => {})

    await expect(copyToClipboard({ value: "abc123" })).resolves.toBe(false)
  })

  it("returns false when the write rejects", async () => {
    const writeText = jest.fn().mockRejectedValue(new Error("denied"))
    defineClipboard({ writeText })
    jest.spyOn(console, "log").mockImplementation(() => {})

    await expect(copyToClipboard({ value: "abc123" })).resolves.toBe(false)
  })
})

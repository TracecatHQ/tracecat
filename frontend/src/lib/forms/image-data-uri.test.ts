import {
  isAllowedImageDataUri,
  MAX_FORM_IMAGE_BYTES,
} from "@/lib/forms/image-data-uri"

function dataUri(subtype: string, bytes: number[]): string {
  return `data:image/${subtype};base64,${btoa(String.fromCharCode(...bytes))}`
}

const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d]
const JPEG = [0xff, 0xd8, 0xff, 0xe0, 0, 0x10, 0x4a, 0x46, 0x49, 0x46, 0, 1]
const WEBP = [0x52, 0x49, 0x46, 0x46, 4, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]

describe("isAllowedImageDataUri", () => {
  it.each([
    ["png", PNG],
    ["jpeg", JPEG],
    ["webp", WEBP],
  ])("accepts an inline %s image", (subtype, bytes) => {
    expect(isAllowedImageDataUri(dataUri(subtype, bytes))).toBe(true)
  })

  it("rejects content that does not match the declared type", () => {
    expect(isAllowedImageDataUri(dataUri("png", JPEG))).toBe(false)
    expect(isAllowedImageDataUri(dataUri("webp", PNG))).toBe(false)
  })

  it.each([
    ["a remote URL", "https://example.com/laptop.png"],
    ["a protocol-relative URL", "//example.com/laptop.png"],
    ["a relative path", "/laptop.png"],
    ["an SVG data URI", `data:image/svg+xml;base64,${btoa("<svg/>")}`],
    ["a GIF data URI", dataUri("gif", [0x47, 0x49, 0x46, 0x38, 0x39, 0x61])],
    ["an HTML data URI", `data:text/html;base64,${btoa("<script>")}`],
    ["a non-base64 data URI", "data:image/png,%89PNG"],
    ["extra parameters", `data:image/png;charset=utf-8;base64,${btoa("x")}`],
    ["invalid base64 characters", "data:image/png;base64,iVBOR w0KGgo="],
    ["a javascript URL", "javascript:alert(1)"],
    ["an empty string", ""],
    ["null", null],
    ["undefined", undefined],
  ])("rejects %s", (_name, value) => {
    expect(isAllowedImageDataUri(value)).toBe(false)
  })

  it("rejects an image over the size limit", () => {
    const tooBig = [...PNG, ...new Array(MAX_FORM_IMAGE_BYTES).fill(0)]
    expect(isAllowedImageDataUri(dataUri("png", tooBig))).toBe(false)
  })
})

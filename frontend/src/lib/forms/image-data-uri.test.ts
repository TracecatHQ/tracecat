import {
  FormImageError,
  findImageDataUris,
  getDataUriByteSize,
  isAllowedImageDataUri,
  MAX_FORM_IMAGE_BYTES,
  prepareFormImage,
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

describe("getDataUriByteSize", () => {
  it.each([1, 2, 3, 10, 11, 12])("counts %i decoded bytes", (length) => {
    const bytes = new Array(length).fill(7)
    expect(getDataUriByteSize(dataUri("png", bytes))).toBe(length)
  })
})

describe("findImageDataUris", () => {
  it("finds each inline image with its size", () => {
    const first = dataUri("webp", WEBP)
    const second = dataUri("png", [...PNG, 1, 2])
    const text = `options:\n  - {value: mba, image: "${first}"}\n  - value: mbp\n    image: ${second}\n`

    const ranges = findImageDataUris(text)

    expect(ranges.map((range) => text.slice(range.from, range.to))).toEqual([
      first,
      second,
    ])
    expect(ranges.map((range) => range.bytes)).toEqual([12, 14])
  })

  it("ignores text that only mentions a data URI", () => {
    expect(findImageDataUris("help: paste a data:image value here")).toEqual([])
  })
})

describe("prepareFormImage", () => {
  function file(type: string, name = "laptop"): File {
    return new File(["pixels"], name, { type })
  }

  it("returns the resized image as a data URI", async () => {
    const encoded = dataUri("webp", WEBP)
    const encode = jest.fn().mockResolvedValue(encoded)
    const picked = file("image/jpeg")

    await expect(prepareFormImage(picked, encode)).resolves.toBe(encoded)
    expect(encode).toHaveBeenCalledWith(picked)
  })

  it.each(["image/gif", "image/svg+xml", "application/pdf", ""])(
    "rejects a %j file without reading it",
    async (type) => {
      const encode = jest.fn()

      await expect(prepareFormImage(file(type), encode)).rejects.toThrow(
        new FormImageError("Use a PNG, JPEG or WebP image.")
      )
      expect(encode).not.toHaveBeenCalled()
    }
  )

  it("rejects an image that is still over the limit after resizing", async () => {
    const tooBig = [...PNG, ...new Array(MAX_FORM_IMAGE_BYTES).fill(0)]
    const encode = jest.fn().mockResolvedValue(dataUri("png", tooBig))

    const result = prepareFormImage(file("image/png"), encode)

    await expect(result).rejects.toBeInstanceOf(FormImageError)
    await expect(result).rejects.toThrow(
      "This image is 101 KB after resizing. The limit is 100 KB. Use a simpler image."
    )
  })

  it("accepts an image exactly at the limit", async () => {
    const atLimit = [
      ...PNG,
      ...new Array(MAX_FORM_IMAGE_BYTES - PNG.length).fill(0),
    ]
    const encoded = dataUri("png", atLimit)

    await expect(
      prepareFormImage(file("image/png"), async () => encoded)
    ).resolves.toBe(encoded)
  })

  it("reports a file the browser cannot decode", async () => {
    const encode = jest.fn().mockRejectedValue(new Error("decode failed"))

    await expect(prepareFormImage(file("image/png"), encode)).rejects.toThrow(
      new FormImageError("This file could not be read as an image.")
    )
  })

  it("rejects an encoder result that is not an allowed image", async () => {
    const encode = jest.fn().mockResolvedValue("data:image/gif;base64,R0lG")

    await expect(
      prepareFormImage(file("image/png"), encode)
    ).rejects.toBeInstanceOf(FormImageError)
  })
})

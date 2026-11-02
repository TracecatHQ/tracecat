import { prepareFormImage } from "@/lib/forms/image-data-uri"

const PNG = `data:image/png;base64,${btoa("\x89PNG\r\n\x1a\n\0\0\0\r")}`
const JPEG = `data:image/jpeg;base64,${btoa("\xff\xd8\xff\xe0\0\x10JFIF\0\x01")}`

/**
 * A canvas without a WebP encoder, as in browsers that answer a WebP export
 * with PNG. Records each draw, fill and export in order.
 */
function fakeCanvas() {
  const steps: string[] = []
  const context = {
    globalCompositeOperation: "source-over",
    fillStyle: "#000000",
    drawImage: () => steps.push("draw"),
    fillRect: () =>
      steps.push(
        `fill ${context.fillStyle} ${context.globalCompositeOperation}`
      ),
  }
  const canvas = {
    width: 0,
    height: 0,
    getContext: () => context,
    toDataURL: (type: string) => {
      steps.push(`export ${type}`)
      return type === "image/jpeg" ? JPEG : PNG
    },
  }
  return { canvas, steps }
}

beforeEach(() => {
  Object.defineProperty(globalThis, "createImageBitmap", {
    configurable: true,
    value: jest.fn().mockResolvedValue({ width: 640, height: 480, close() {} }),
  })
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe("prepareFormImage", () => {
  it("paints a transparent WebP onto white before falling back to JPEG", async () => {
    const { canvas, steps } = fakeCanvas()
    const createElement = document.createElement.bind(document)
    jest
      .spyOn(document, "createElement")
      .mockImplementation((tag: string) =>
        tag === "canvas"
          ? (canvas as unknown as HTMLCanvasElement)
          : createElement(tag)
      )
    const file = new File(["x"], "logo.webp", { type: "image/webp" })

    expect(await prepareFormImage(file)).toBe(JPEG)
    // JPEG has no alpha: without the white fill, transparent pixels go black.
    expect(steps).toEqual([
      "draw",
      "export image/webp",
      "fill #fff destination-over",
      "export image/jpeg",
    ])
  })
})

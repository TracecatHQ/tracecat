import { Schema } from "@tiptap/pm/model"
import { EditorState } from "@tiptap/pm/state"
import {
  mapImageUploadPosition,
  sanitizeMarkdownImageAlt,
  transactionTouchesImageReplacement,
} from "@/lib/tiptap-image-upload-position"

describe("TipTap image upload position", () => {
  it("maps a pending insertion point through intervening edits", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "text*", group: "block" },
        text: {},
      },
    })
    const state = EditorState.create({
      schema,
      doc: schema.node("doc", null, [
        schema.node("paragraph", null, schema.text("before after")),
      ]),
    })
    const dropPosition = 8
    const transaction = state.tr.insertText("new ", 1)

    expect(mapImageUploadPosition(dropPosition, transaction)).toBe(12)
  })

  it("maps both ends of a pending image replacement", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "text*", group: "block" },
        text: {},
      },
    })
    const state = EditorState.create({
      schema,
      doc: schema.node("doc", null, [
        schema.node("paragraph", null, schema.text("before selected after")),
      ]),
    })
    const startTransaction = state.tr.insertText("new ", 8)
    const endTransaction = state.tr.insertText("new ", 16)

    expect(mapImageUploadPosition(8, startTransaction, 1)).toBe(12)
    expect(mapImageUploadPosition(8, startTransaction, -1)).toBe(8)
    expect(mapImageUploadPosition(16, endTransaction, -1)).toBe(16)
    expect(mapImageUploadPosition(16, endTransaction, 1)).toBe(20)
  })

  it("detects edits inside a pending image replacement", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "text*", group: "block" },
        text: {},
      },
    })
    const state = EditorState.create({
      schema,
      doc: schema.node("doc", null, [
        schema.node("paragraph", null, schema.text("before selected after")),
      ]),
    })

    expect(
      transactionTouchesImageReplacement(8, 16, state.tr.insertText("x", 12))
    ).toBe(true)
    expect(
      transactionTouchesImageReplacement(8, 16, state.tr.insertText("x", 1))
    ).toBe(false)
  })

  it("sanitizes image filenames that would break Markdown alt syntax", () => {
    expect(sanitizeMarkdownImageAlt("report[final]\\copy.png")).toBe(
      "report_final__copy.png"
    )
  })
})

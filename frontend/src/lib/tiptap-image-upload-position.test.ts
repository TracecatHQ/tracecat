import { Schema } from "@tiptap/pm/model"
import { EditorState } from "@tiptap/pm/state"
import {
  deleteImagePasteSelection,
  mapImageUploadPosition,
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

  it("deletes the active selection before inserting a pasted image", () => {
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
    const transaction = deleteImagePasteSelection(state.tr, 8, 16)

    expect(transaction).not.toBeNull()
    expect(transaction?.doc.textContent).toBe("before  after")
  })

  it("does not create a transaction for an empty selection", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "text*", group: "block" },
        text: {},
      },
    })
    const state = EditorState.create({ schema })

    expect(deleteImagePasteSelection(state.tr, 1, 1)).toBeNull()
  })
})

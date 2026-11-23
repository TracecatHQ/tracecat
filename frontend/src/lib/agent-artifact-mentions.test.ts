import {
  mentionFromHref,
  parseArtifactMention,
  remarkArtifactMentions,
} from "@/lib/agent-artifact-mentions"

const CASE_ID = "11111111-1111-4111-8111-111111111111"
const FORM_ID = "22222222-2222-4222-8222-222222222222"
const CASE_URI = `tracecat-ref://v1/artifact/case/${CASE_ID}`
const FORM_URI = `tracecat-ref://v1/artifact/form/${FORM_ID}`

describe("parseArtifactMention", () => {
  it("reads case mentions", () => {
    expect(parseArtifactMention(CASE_URI)).toEqual({
      type: "case",
      id: CASE_ID,
    })
  })

  it.each([
    FORM_URI,
    `tracecat-ref://v1/artifact/chart/${CASE_ID}`,
    `tracecat-ref://v1/table/${CASE_ID}`,
    "tracecat-ref://v1/artifact/case/CASE-0142",
    "https://example.com",
  ])("is not a mention: %s", (uri) => {
    expect(parseArtifactMention(uri)).toBeNull()
  })
})

describe("remarkArtifactMentions", () => {
  const link = (url: string, text: string) => ({
    type: "link",
    url,
    children: [{ type: "text", value: text }],
  })

  it("keeps case mentions as links, drops other reserved links to text", () => {
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [
            link(CASE_URI, "CASE-0142"),
            link(FORM_URI, "Intake"),
            link(`tracecat-ref://v1/artifact/chart/${CASE_ID}`, "Chart"),
            link("https://example.com", "docs"),
          ],
        },
      ],
    }

    remarkArtifactMentions()(tree)

    const [mention, form, chart, docs] = tree.children[0].children
    expect(mentionFromHref(mention.url)).toEqual({ type: "case", id: CASE_ID })
    expect(form).toEqual({ type: "text", value: "Intake" })
    expect(chart).toEqual({ type: "text", value: "Chart" })
    expect(docs).toEqual(link("https://example.com", "docs"))
  })

  it("never shows an autolink's URI as its text", () => {
    const formURI = FORM_URI
    const tree = {
      type: "root",
      children: [
        {
          type: "paragraph",
          children: [link(CASE_URI, CASE_URI), link(formURI, formURI)],
        },
      ],
    }

    remarkArtifactMentions()(tree)

    const [mention, form] = tree.children[0].children
    expect(mention.children).toEqual([{ type: "text", value: "case" }])
    expect(form).toEqual({ type: "text", value: "case" })
  })

  it("never reads an ordinary path as a mention", () => {
    expect(mentionFromHref(`/workspaces/ws/cases/${CASE_ID}`)).toBeNull()
    expect(mentionFromHref(undefined)).toBeNull()
  })
})

import { Schema } from "@tiptap/pm/model"
import {
  AGENT_MENTION_URI_SCHEME,
  buildAgentMentionHref,
  buildWorkflowMentionHref,
  commentMentionLeafText,
  findCommentMentionLinkRanges,
  findEditedCommentMentionIndexes,
  isCommentMentionHref,
  nodeAllowsCommentMention,
  preventCommentMentionNavigation,
  serializeTiptapComment,
} from "@/lib/tiptap-comment-mentions"

describe("TipTap comment mention serialization", () => {
  const workflowId = "11111111-1111-4111-8111-111111111111"

  it("keeps the existing agent mention wire format intact", () => {
    const agentId = "22222222-2222-4222-8222-222222222222"
    const markdown = `Ask [@Response agent](${buildAgentMentionHref(agentId)}) for help`

    expect(serializeTiptapComment(markdown)).toEqual({
      content: markdown,
      workflowId: null,
    })
    expect(buildAgentMentionHref(agentId)).toBe(
      `${AGENT_MENTION_URI_SCHEME}${agentId}`
    )
  })

  it("removes a workflow marker and returns its request id", () => {
    expect(
      serializeTiptapComment(
        `[/Enrich case](${buildWorkflowMentionHref(workflowId)}) investigate this`
      )
    ).toEqual({
      content: "investigate this",
      workflowId,
    })
  })

  it("allows a bare workflow command to produce an empty body", () => {
    expect(
      serializeTiptapComment(
        `[/Enrich case](${buildWorkflowMentionHref(workflowId)})`
      )
    ).toEqual({ content: "", workflowId })
  })

  it.each([
    ["bullet item", "- %s"],
    ["task item", "- [ ] %s"],
    ["block quote", "> %s"],
    ["heading", "### %s"],
    ["strong text", "**%s**"],
  ])("removes an empty %s around a workflow marker", (_label, template) => {
    const marker = `[/Enrich case](${buildWorkflowMentionHref(workflowId)})`

    expect(serializeTiptapComment(template.replace("%s", marker))).toEqual({
      content: "",
      workflowId,
    })
  })

  it("preserves a rich-text container when it has other content", () => {
    const marker = `[/Enrich case](${buildWorkflowMentionHref(workflowId)})`

    expect(serializeTiptapComment(`- ${marker} investigate this`)).toEqual({
      content: "- investigate this",
      workflowId,
    })
  })

  it("handles escaped closing brackets in workflow labels", () => {
    expect(
      serializeTiptapComment(
        `Before [/Enrich \\] case](${buildWorkflowMentionHref(workflowId)}) after`
      )
    ).toEqual({ content: "Before after", workflowId })
  })

  it("does not change ordinary links or text containing slash commands", () => {
    const markdown = "Run /Enrich or read [the docs](https://example.com)"
    expect(serializeTiptapComment(markdown)).toEqual({
      content: markdown,
      workflowId: null,
    })
  })

  it("recognizes only the two internal mention link schemes", () => {
    expect(isCommentMentionHref(buildAgentMentionHref("agent-id"))).toBe(true)
    expect(isCommentMentionHref(buildWorkflowMentionHref("workflow-id"))).toBe(
      true
    )
    expect(isCommentMentionHref("https://example.com")).toBe(false)
  })

  it.each([
    buildAgentMentionHref("agent-id"),
    buildWorkflowMentionHref("workflow-id"),
  ])("prevents native navigation for %s links", (href) => {
    const anchor = document.createElement("a")
    const label = document.createElement("span")
    anchor.href = href
    anchor.append(label)
    const preventDefault = jest.fn()

    expect(
      preventCommentMentionNavigation({
        target: label,
        preventDefault,
      } as unknown as MouseEvent)
    ).toBe(true)
    expect(preventDefault).toHaveBeenCalledTimes(1)
  })

  it("does not intercept ordinary links", () => {
    const anchor = document.createElement("a")
    anchor.href = "https://example.com"
    const preventDefault = jest.fn()

    expect(
      preventCommentMentionNavigation({
        target: anchor,
        preventDefault,
      } as unknown as MouseEvent)
    ).toBe(false)
    expect(preventDefault).not.toHaveBeenCalled()
  })

  it("treats a hard break as whitespace when scanning for mentions", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "inline*", group: "block" },
        text: { group: "inline" },
        hardBreak: { inline: true, group: "inline" },
      },
    })
    const paragraph = schema.node("paragraph", null, [
      schema.text("First line"),
      schema.node("hardBreak"),
      schema.text("@triage"),
    ])

    expect(
      paragraph.textBetween(
        0,
        paragraph.content.size,
        "\n",
        commentMentionLeafText
      )
    ).toBe("First line\n@triage")
  })

  it("recognizes text blocks that can carry mention links", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "inline*", group: "block" },
        codeBlock: {
          content: "text*",
          group: "block",
          marks: "",
          code: true,
        },
        text: { group: "inline" },
      },
      marks: { link: { attrs: { href: {} } } },
    })

    expect(
      nodeAllowsCommentMention(schema.node("paragraph"), schema.marks.link)
    ).toBe(true)
    expect(
      nodeAllowsCommentMention(schema.node("codeBlock"), schema.marks.link)
    ).toBe(false)
  })

  it("marks mention links that carry additional formatting", () => {
    const schema = new Schema({
      nodes: {
        doc: { content: "block+" },
        paragraph: { content: "inline*", group: "block" },
        text: { group: "inline" },
      },
      marks: {
        link: { attrs: { href: {} } },
        strong: {},
      },
    })
    const href = buildAgentMentionHref("agent-id")
    const paragraph = schema.node("paragraph", null, [
      schema.text("@Triage agent", [
        schema.mark("link", { href }),
        schema.mark("strong"),
      ]),
    ])
    const doc = schema.node("doc", null, [paragraph])

    expect(findCommentMentionLinkRanges(doc)).toEqual([
      {
        from: 1,
        to: 14,
        href,
        text: "@Triage agent",
        hasFormatting: true,
      },
    ])
  })

  it("detects a selected mention whose visible label was edited", () => {
    expect(
      findEditedCommentMentionIndexes(
        [
          {
            href: buildAgentMentionHref("agent-id"),
            text: "@Triage agent",
            hasFormatting: false,
          },
        ],
        [
          {
            href: buildAgentMentionHref("agent-id"),
            text: "@Triage agnt",
            hasFormatting: false,
          },
        ]
      )
    ).toEqual([0])
  })

  it("detects a mention with an additional formatting mark", () => {
    const mention = {
      href: buildAgentMentionHref("agent-id"),
      text: "@Triage agent",
    }

    expect(
      findEditedCommentMentionIndexes(
        [{ ...mention, hasFormatting: false }],
        [{ ...mention, hasFormatting: true }]
      )
    ).toEqual([0])
  })

  it("detects every range when a line break splits a mention link", () => {
    const href = buildAgentMentionHref("agent-id")

    expect(
      findEditedCommentMentionIndexes(
        [{ href, text: "@Triage agent", hasFormatting: false }],
        [
          { href, text: "@Triage", hasFormatting: false },
          { href, text: " agent", hasFormatting: false },
        ]
      )
    ).toEqual([0, 1])
  })

  it("does not treat an additional intact mention as a split", () => {
    const mention = {
      href: buildAgentMentionHref("agent-id"),
      text: "@Triage agent",
      hasFormatting: false,
    }

    expect(
      findEditedCommentMentionIndexes([mention], [mention, mention])
    ).toEqual([])
  })

  it("matches an edited mention after a different mention is removed", () => {
    const agentMention = {
      href: buildAgentMentionHref("agent-id"),
      text: "@Triage agent",
      hasFormatting: false,
    }
    const workflowMention = {
      href: buildWorkflowMentionHref("workflow-id"),
      text: "/Enrich case",
      hasFormatting: false,
    }

    expect(
      findEditedCommentMentionIndexes(
        [agentMention, workflowMention],
        [{ ...workflowMention, text: "/Enrich" }]
      )
    ).toEqual([0])
  })

  it("does not treat inserted or replaced mentions as label edits", () => {
    expect(
      findEditedCommentMentionIndexes(
        [],
        [
          {
            href: buildAgentMentionHref("agent-id"),
            text: "@Triage agent",
            hasFormatting: false,
          },
        ]
      )
    ).toEqual([])
    expect(
      findEditedCommentMentionIndexes(
        [
          {
            href: buildAgentMentionHref("old-agent"),
            text: "@Old agent",
            hasFormatting: false,
          },
        ],
        [
          {
            href: buildAgentMentionHref("new-agent"),
            text: "@New agent",
            hasFormatting: false,
          },
        ]
      )
    ).toEqual([])
  })
})

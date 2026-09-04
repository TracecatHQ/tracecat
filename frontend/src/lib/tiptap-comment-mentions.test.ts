import {
  AGENT_MENTION_URI_SCHEME,
  buildAgentMentionHref,
  buildWorkflowMentionHref,
  isCommentMentionHref,
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
})

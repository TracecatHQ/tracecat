import {
  applyMentionInsertion,
  applyMentionRemoval,
  buildMentionSegments,
  diffTextSplice,
  findAgentMention,
  findMentionEndingAt,
  findWorkflowMention,
  formatAgentMentionToken,
  getMentionToken,
  type MentionKind,
  type MentionRange,
  mentionDisplayText,
  remapMentions,
  serializeMentions,
} from "@/lib/mentions"

function mention(
  start: number,
  label: string,
  targetId = "preset-1",
  kind: MentionKind = "agent"
): MentionRange {
  return {
    start,
    end: start + mentionDisplayText(kind, label).length,
    kind,
    label,
    targetId,
  }
}

function workflow(
  start: number,
  label: string,
  targetId = "workflow-1"
): MentionRange {
  return mention(start, label, targetId, "workflow")
}

describe("mentionDisplayText", () => {
  it("prefixes agents with @ and workflows with /", () => {
    expect(mentionDisplayText("agent", "Triage agent")).toBe("@Triage agent")
    expect(mentionDisplayText("workflow", "Escalate case")).toBe(
      "/Escalate case"
    )
  })
})

describe("formatAgentMentionToken", () => {
  it("renders the shared wire token format", () => {
    expect(
      formatAgentMentionToken({ label: "Triage agent", targetId: "preset-1" })
    ).toBe("[@Triage agent](mention://agent/preset-1)")
  })
})

describe("serializeMentions", () => {
  it("returns the text unchanged when there are no mentions", () => {
    expect(serializeMentions("plain comment", [])).toBe("plain comment")
  })

  it("serializes a mention surrounded by text", () => {
    const text = "Ping @Triage agent now"
    expect(serializeMentions(text, [mention(5, "Triage agent")])).toBe(
      "Ping [@Triage agent](mention://agent/preset-1) now"
    )
  })

  it("serializes a mention at the start and at the end", () => {
    expect(
      serializeMentions("@Triage agent ok", [mention(0, "Triage agent")])
    ).toBe("[@Triage agent](mention://agent/preset-1) ok")
    expect(
      serializeMentions("ok @Triage agent", [mention(3, "Triage agent")])
    ).toBe("ok [@Triage agent](mention://agent/preset-1)")
  })

  it("serializes multiple mentions, including adjacent ones", () => {
    const text = "@Triage agent@Malware agent tail"
    expect(
      serializeMentions(text, [
        mention(0, "Triage agent"),
        mention(13, "Malware agent", "preset-2"),
      ])
    ).toBe(
      "[@Triage agent](mention://agent/preset-1)[@Malware agent](mention://agent/preset-2) tail"
    )
  })

  it("ignores ranges that fall outside the text", () => {
    expect(serializeMentions("short", [mention(40, "Triage agent")])).toBe(
      "short"
    )
  })

  it("strips a workflow command and one following space", () => {
    expect(
      serializeMentions("/Escalate case hello", [workflow(0, "Escalate case")])
    ).toBe("hello")
    expect(
      serializeMentions("hello /Escalate case", [workflow(6, "Escalate case")])
    ).toBe("hello ")
    expect(
      serializeMentions("a /Escalate case  b", [workflow(2, "Escalate case")])
    ).toBe("a  b")
  })

  it("strips workflow commands while keeping agent tokens", () => {
    expect(
      serializeMentions("/Escalate case ping @Triage agent", [
        workflow(0, "Escalate case"),
        mention(20, "Triage agent"),
      ])
    ).toBe("ping [@Triage agent](mention://agent/preset-1)")
  })
})

describe("findWorkflowMention", () => {
  it("returns the workflow range, ignoring agents", () => {
    const target = workflow(6, "Escalate case")
    expect(findWorkflowMention([mention(0, "Triage agent"), target])).toBe(
      target
    )
    expect(findWorkflowMention([mention(0, "Triage agent")])).toBeUndefined()
  })
})

describe("findAgentMention", () => {
  it("returns the agent range, ignoring workflows", () => {
    const target = mention(0, "Triage agent")
    expect(findAgentMention([target, workflow(14, "Escalate case")])).toBe(
      target
    )
    expect(findAgentMention([workflow(0, "Escalate case")])).toBeUndefined()
  })

  it("returns the first agent when several survive", () => {
    const first = mention(0, "Triage agent")
    const second = mention(20, "Malware agent")
    expect(findAgentMention([first, second])).toBe(first)
  })

  it("returns undefined for an empty range list", () => {
    expect(findAgentMention([])).toBeUndefined()
  })
})

describe("diffTextSplice", () => {
  it("describes an insertion at the caret", () => {
    expect(diffTextSplice("ab", "aXb", 2)).toEqual({
      start: 1,
      deleted: 0,
      inserted: 1,
    })
  })

  it("describes a single-character deletion", () => {
    expect(diffTextSplice("aXb", "ab", 1)).toEqual({
      start: 1,
      deleted: 1,
      inserted: 0,
    })
  })

  it("uses the caret to disambiguate repeated characters", () => {
    expect(diffTextSplice("aa", "aaa", 1)).toEqual({
      start: 0,
      deleted: 0,
      inserted: 1,
    })
  })
})

describe("remapMentions", () => {
  const target = mention(5, "Triage agent")

  it("shifts a mention when text is inserted before it", () => {
    const [remapped] = remapMentions([target], {
      start: 0,
      deleted: 0,
      inserted: 3,
    })
    expect(remapped).toMatchObject({ start: 8, end: target.end + 3 })
  })

  it("leaves a mention untouched when text is inserted after it", () => {
    expect(
      remapMentions([target], { start: target.end, deleted: 0, inserted: 4 })
    ).toEqual([target])
  })

  it("shifts a mention when the insertion sits exactly at its start", () => {
    const [remapped] = remapMentions([target], {
      start: target.start,
      deleted: 0,
      inserted: 2,
    })
    expect(remapped).toMatchObject({ start: 7 })
  })

  it("dissolves a mention when an edit lands inside it", () => {
    expect(
      remapMentions([target], { start: 7, deleted: 0, inserted: 1 })
    ).toEqual([])
  })

  it("dissolves a mention when a deletion overlaps its edge", () => {
    expect(
      remapMentions([target], { start: 4, deleted: 3, inserted: 0 })
    ).toEqual([])
  })

  it("remaps every mention independently", () => {
    const second = mention(30, "Malware agent", "preset-2")
    const remapped = remapMentions([target, second], {
      start: 0,
      deleted: 0,
      inserted: 2,
    })
    expect(remapped).toHaveLength(2)
    expect(remapped[1]).toMatchObject({ start: 32 })
  })
})

describe("findMentionEndingAt", () => {
  const target = mention(5, "Triage agent")

  it("finds a mention that ends at the caret", () => {
    expect(findMentionEndingAt([target], target.end)).toBe(target)
  })

  it("returns undefined elsewhere in the text", () => {
    expect(findMentionEndingAt([target], target.end + 1)).toBeUndefined()
    expect(findMentionEndingAt([target], target.start)).toBeUndefined()
  })
})

describe("buildMentionSegments", () => {
  it("splits text into plain and mention runs", () => {
    const segments = buildMentionSegments("Ping @Triage agent now", [
      mention(5, "Triage agent"),
    ])
    expect(segments).toEqual([
      { start: 0, text: "Ping ", mention: undefined },
      {
        start: 5,
        text: "@Triage agent",
        mention: mention(5, "Triage agent"),
      },
      { start: 18, text: " now", mention: undefined },
    ])
  })

  it("returns a single plain run when there are no mentions", () => {
    expect(buildMentionSegments("plain", [])).toEqual([
      { start: 0, text: "plain", mention: undefined },
    ])
  })
})

describe("getMentionToken", () => {
  it("matches @ at the start of the text", () => {
    expect(getMentionToken("@tri", 4)).toEqual({
      start: 0,
      end: 4,
      query: "tri",
      kind: "agent",
    })
  })

  it("matches @ after whitespace", () => {
    expect(getMentionToken("ping @tri", 9)).toEqual({
      start: 5,
      end: 9,
      query: "tri",
      kind: "agent",
    })
  })

  it("returns undefined without a trigger or after a non-space", () => {
    expect(getMentionToken("ping", 4)).toBeUndefined()
    expect(getMentionToken("email@tri", 9)).toBeUndefined()
  })

  it("carries spaces so a multi-word name can be typed out", () => {
    expect(getMentionToken("@Triage ana", 11)).toEqual({
      start: 0,
      end: 11,
      query: "Triage ana",
      kind: "agent",
    })
  })

  it("ends the query at a leading space, a newline, or the length cap", () => {
    expect(getMentionToken("@ triage", 8)).toBeUndefined()
    expect(getMentionToken("@tri\nagent", 10)).toBeUndefined()
    expect(getMentionToken(`@${"a".repeat(65)}`, 66)).toBeUndefined()
    expect(getMentionToken(`@${"a".repeat(64)}`, 65)).toEqual({
      start: 0,
      end: 65,
      query: "a".repeat(64),
      kind: "agent",
    })
  })

  it("ignores text after the caret", () => {
    expect(getMentionToken("@tri tail", 4)).toEqual({
      start: 0,
      end: 4,
      query: "tri",
      kind: "agent",
    })
  })

  it("matches / at the start of the text and after whitespace", () => {
    expect(getMentionToken("/esc", 4)).toEqual({
      start: 0,
      end: 4,
      query: "esc",
      kind: "workflow",
    })
    expect(getMentionToken("run /esc", 8)).toEqual({
      start: 4,
      end: 8,
      query: "esc",
      kind: "workflow",
    })
  })

  it("ignores / inside words, paths, and URLs", () => {
    expect(getMentionToken("a/b", 3)).toBeUndefined()
    expect(getMentionToken("https://x.com/path", 18)).toBeUndefined()
    expect(getMentionToken("see https://x.com/path", 22)).toBeUndefined()
  })

  it("resolves @bar/baz to the agent token", () => {
    expect(getMentionToken("@bar/baz", 8)).toEqual({
      start: 0,
      end: 8,
      query: "bar/baz",
      kind: "agent",
    })
  })

  it("resolves /foo@bar to the workflow token", () => {
    expect(getMentionToken("/foo@bar", 8)).toEqual({
      start: 0,
      end: 8,
      query: "foo@bar",
      kind: "workflow",
    })
  })
})

describe("applyMentionInsertion", () => {
  it("replaces the @query with display text and registers the range", () => {
    const edit = applyMentionInsertion(
      "Ping @tri now",
      [],
      { start: 5, end: 9, query: "tri", kind: "agent" },
      { kind: "agent", label: "Triage agent", targetId: "preset-1" }
    )
    expect(edit.text).toBe("Ping @Triage agent  now")
    expect(edit.mentions).toEqual([mention(5, "Triage agent")])
    expect(edit.caret).toBe("Ping @Triage agent ".length)
  })

  it("shifts existing mentions that follow the insertion point", () => {
    const later = mention(10, "Malware agent", "preset-2")
    const edit = applyMentionInsertion(
      "Ping @tri x",
      [later],
      { start: 5, end: 9, query: "tri", kind: "agent" },
      { kind: "agent", label: "Triage agent", targetId: "preset-1" }
    )
    expect(edit.mentions).toEqual([
      { ...later, start: 20, end: later.end + 10 },
      mention(5, "Triage agent"),
    ])
  })

  it("inserts a workflow command with a / prefix", () => {
    const edit = applyMentionInsertion(
      "/esc",
      [],
      { start: 0, end: 4, query: "esc", kind: "workflow" },
      { kind: "workflow", label: "Escalate case", targetId: "workflow-1" }
    )
    expect(edit.text).toBe("/Escalate case ")
    expect(edit.mentions).toEqual([workflow(0, "Escalate case")])
    expect(edit.caret).toBe("/Escalate case ".length)
  })

  it("replaces an existing workflow that sits before the token", () => {
    const existing = workflow(0, "Escalate case")
    const edit = applyMentionInsertion(
      "/Escalate case hello /clo",
      [existing],
      { start: 21, end: 25, query: "clo", kind: "workflow" },
      { kind: "workflow", label: "Close case", targetId: "workflow-2" }
    )
    expect(edit.text).toBe(" hello /Close case ")
    expect(edit.mentions).toEqual([workflow(7, "Close case", "workflow-2")])
    expect(edit.caret).toBe(" hello /Close case ".length)
  })

  it("replaces an existing workflow that sits after the token", () => {
    const existing = workflow(11, "Escalate case")
    const edit = applyMentionInsertion(
      "/clo hello /Escalate case",
      [existing],
      { start: 0, end: 4, query: "clo", kind: "workflow" },
      { kind: "workflow", label: "Close case", targetId: "workflow-2" }
    )
    expect(edit.text).toBe("/Close case  hello ")
    expect(edit.mentions).toEqual([workflow(0, "Close case", "workflow-2")])
  })

  it("keeps agent mentions when a workflow replaces another", () => {
    const agent = mention(15, "Triage agent")
    const edit = applyMentionInsertion(
      "/Escalate case @Triage agent /clo",
      [workflow(0, "Escalate case"), agent],
      { start: 29, end: 33, query: "clo", kind: "workflow" },
      { kind: "workflow", label: "Close case", targetId: "workflow-2" }
    )
    expect(edit.text).toBe(" @Triage agent /Close case ")
    expect(edit.mentions).toEqual([
      { ...agent, start: 1, end: 14 },
      workflow(15, "Close case", "workflow-2"),
    ])
  })
})

describe("applyMentionRemoval", () => {
  it("removes the mention text and range, landing the caret at its start", () => {
    const edit = applyMentionRemoval(
      "Ping @Triage agent now",
      [mention(5, "Triage agent")],
      mention(5, "Triage agent")
    )
    expect(edit).toEqual({ text: "Ping  now", mentions: [], caret: 5 })
  })
})

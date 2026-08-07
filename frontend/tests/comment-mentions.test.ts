import {
  buildMentionSegments,
  diffTextSplice,
  findMentionEndingAt,
  formatAgentMentionToken,
  type MentionRange,
  mentionDisplayText,
  remapMentions,
  serializeMentions,
} from "@/lib/comment-mentions"

function mention(
  start: number,
  label: string,
  targetId = "preset-1"
): MentionRange {
  return {
    start,
    end: start + mentionDisplayText(label).length,
    label,
    targetId,
  }
}

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

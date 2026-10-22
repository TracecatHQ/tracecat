import type { UIMessage } from "ai"
import { serverTookPrompt } from "@/lib/chat-dropped-prompt"

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))

const prompt: UIMessage = {
  id: "u2",
  role: "user",
  parts: [{ type: "text", text: "Summarize it" }],
}

function probe(status: number, transcript: UIMessage[] = []) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: status < 400,
    status,
    body: null,
  }) as unknown as typeof fetch
  return serverTookPrompt({
    chatId: "chat-1",
    workspaceId: "workspace-1",
    prompt,
    refetchTranscript: async () => transcript,
  })
}

describe("serverTookPrompt", () => {
  it("is true while the server still streams the turn", async () => {
    await expect(probe(200)).resolves.toBe(true)
  })

  it("is true when the finished transcript holds the prompt", async () => {
    await expect(probe(204, [{ ...prompt, id: "server-u2" }])).resolves.toBe(
      true
    )
  })

  it("is false when the server never got the prompt", async () => {
    await expect(probe(204)).resolves.toBe(false)
  })

  it("throws rather than guessing when the check fails", async () => {
    await expect(probe(502)).rejects.toThrow("Turn check failed with 502")
  })
})

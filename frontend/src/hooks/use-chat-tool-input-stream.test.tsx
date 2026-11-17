import { TextDecoderStream } from "node:stream/web"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { useVercelChat } from "@/hooks/use-chat"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/lib/api", () => ({ getBaseUrl: () => "https://example.invalid" }))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

if (typeof globalThis.structuredClone !== "function") {
  globalThis.structuredClone = ((value: unknown) =>
    value === undefined
      ? undefined
      : JSON.parse(JSON.stringify(value))) as typeof structuredClone
}
if (typeof globalThis.Response !== "function") {
  globalThis.Response = class {} as unknown as typeof Response
}
if (typeof globalThis.TextDecoderStream !== "function") {
  globalThis.TextDecoderStream =
    TextDecoderStream as unknown as typeof globalThis.TextDecoderStream
}

function sseBody(chunks: object[]) {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(chunk)}\n\n`))
      }
      controller.enqueue(encoder.encode("data: [DONE]\n\n"))
      controller.close()
    },
  })
}

const toolInput = JSON.stringify({
  preset_id: "preset-1",
  instructions: "lorem ipsum dolor sit amet, ".repeat(1100),
})

function toolInputChunks(input: string, fragmentSize: number): object[] {
  const chunks: object[] = [
    { type: "start", messageId: "reply-1" },
    { type: "start-step" },
    {
      type: "tool-input-start",
      toolCallId: "call-1",
      toolName: "update_preset",
      dynamic: true,
    },
  ]
  for (let i = 0; i < input.length; i += fragmentSize) {
    chunks.push({
      type: "tool-input-delta",
      toolCallId: "call-1",
      inputTextDelta: input.slice(i, i + fragmentSize),
    })
  }
  chunks.push(
    {
      type: "tool-input-available",
      toolCallId: "call-1",
      toolName: "update_preset",
      input: JSON.parse(input),
      dynamic: true,
    },
    { type: "finish-step" },
    { type: "finish" }
  )
  return chunks
}

it("does not re-render once per fragment of a large streamed tool input", async () => {
  const chunks = toolInputChunks(toolInput, 8)
  globalThis.fetch = jest.fn(async () => ({
    ok: true,
    status: 200,
    body: sseBody(chunks),
    text: async () => "",
  })) as unknown as typeof fetch

  let renders = 0
  const queryClient = new QueryClient()
  const { result } = renderHook(
    () => {
      renders++
      return useVercelChat({
        chatId: "chat-1",
        workspaceId: "workspace-1",
        messages: [],
        modelInfo: { name: "model", provider: "provider" },
      })
    },
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      ),
    }
  )
  await waitFor(() => expect(result.current.status).toBe("ready"))
  await act(async () => {})

  // Thousands of fragments used to mean thousands of renders.
  expect(chunks.length).toBeGreaterThan(3_000)
  expect(renders).toBeLessThan(50)
  expect(result.current.messages.at(-1)?.parts).toContainEqual(
    expect.objectContaining({
      toolCallId: "call-1",
      state: "input-available",
      input: JSON.parse(toolInput),
    })
  )
})

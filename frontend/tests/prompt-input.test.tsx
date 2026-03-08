import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { FormEvent } from "react"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input"

describe("PromptInput", () => {
  beforeEach(() => {
    const originalError = console.error
    jest.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
      const message = args[0]
      if (
        typeof message === "string" &&
        message.includes("validateDOMNesting")
      ) {
        return
      }
      originalError(...args)
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it("keeps local input text when async submit fails", async () => {
    const onSubmit = jest.fn().mockRejectedValue(new Error("submit failed"))

    render(
      <PromptInput onSubmit={onSubmit}>
        <PromptInputBody>
          <PromptInputTextarea />
        </PromptInputBody>
        <PromptInputFooter>
          <PromptInputSubmit status="ready" />
        </PromptInputFooter>
      </PromptInput>
    )

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "retry this" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1)
    })

    expect(textarea).toHaveValue("retry this")
  })

  it("submits prompt input inside agent settings forms without triggering outer form submit", async () => {
    const promptSubmit = jest.fn().mockResolvedValue(undefined)
    const outerSubmit = jest.fn((event: FormEvent<HTMLFormElement>) =>
      event.preventDefault()
    )

    render(
      <form onSubmit={outerSubmit}>
        <PromptInput onSubmit={promptSubmit}>
          <PromptInputBody>
            <PromptInputTextarea />
          </PromptInputBody>
          <PromptInputFooter>
            <PromptInputSubmit status="ready" />
          </PromptInputFooter>
        </PromptInput>
      </form>
    )

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "send from agents" } })
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter" })

    await waitFor(() => {
      expect(promptSubmit).toHaveBeenCalledTimes(1)
    })
    expect(outerSubmit).not.toHaveBeenCalled()
  })

  it("treats Cmd/Ctrl+Enter as prompt submit without outer form navigation", async () => {
    const promptSubmit = jest.fn().mockResolvedValue(undefined)
    const outerSubmit = jest.fn((event: FormEvent<HTMLFormElement>) =>
      event.preventDefault()
    )

    render(
      <form onSubmit={outerSubmit}>
        <PromptInput onSubmit={promptSubmit}>
          <PromptInputBody>
            <PromptInputTextarea />
          </PromptInputBody>
          <PromptInputFooter>
            <PromptInputSubmit status="ready" />
          </PromptInputFooter>
        </PromptInput>
      </form>
    )

    const textarea = screen.getByRole("textbox")
    fireEvent.change(textarea, { target: { value: "cmd enter" } })
    fireEvent.keyDown(textarea, {
      code: "Enter",
      key: "Enter",
      metaKey: true,
    })

    await waitFor(() => {
      expect(promptSubmit).toHaveBeenCalledTimes(1)
    })
    expect(outerSubmit).not.toHaveBeenCalled()
  })
})

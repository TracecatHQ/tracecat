import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import {
  PromptInput,
  PromptInputBody,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input"

describe("PromptInput", () => {
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
})

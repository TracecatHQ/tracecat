import { render, screen } from "@testing-library/react"
import { TurnErrorLine } from "@/components/chat/chat-turn-error"

describe("TurnErrorLine", () => {
  it("shows the raw error without model detail", () => {
    render(<TurnErrorLine error="Stream read error" dropped={false} />)
    expect(screen.getByRole("alert")).toHaveTextContent("Stream read error")
  })

  it("turns a model error into an actionable message", () => {
    render(
      <TurnErrorLine
        error="model_error"
        dropped={false}
        modelError={{
          code: "provider_auth_failed",
          provider: "openai",
          model: "gpt-test",
          status_code: 401,
        }}
      />
    )
    expect(
      screen.getByText("OpenAI rejected the API key for gpt-test.")
    ).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: /agent settings/i })
    ).toHaveAttribute("href", "/organization/settings/agent")
    expect(screen.queryByText(/HTTP 401/)).not.toBeInTheDocument()
    // The alert announces the message only; its controls sit outside it.
    const alert = screen.getByRole("alert")
    expect(alert).toHaveTextContent("OpenAI rejected the API key for gpt-test.")
    expect(alert.querySelector("a, button")).toBeNull()
  })

  it("keeps a dropped connection neutral even with model detail", () => {
    render(
      <TurnErrorLine
        error="Connection dropped"
        dropped
        modelError={{ code: "provider_auth_failed" }}
      />
    )
    expect(screen.getByText("Connection dropped")).toBeInTheDocument()
    expect(screen.queryByText(/rejected the API key/)).not.toBeInTheDocument()
  })
})

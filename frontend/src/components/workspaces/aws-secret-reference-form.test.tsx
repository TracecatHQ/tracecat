import { fireEvent, render, screen } from "@testing-library/react"
import { AwsSecretReferenceForm } from "@/components/workspaces/aws-secret-reference-form"

const mockCreateReference = jest.fn()

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-synthetic",
}))
jest.mock("@/hooks/use-secret-stores", () => ({
  useAuthorizedSecretStores: () => ({
    stores: [
      {
        id: "store-synthetic",
        name: "prod",
        provider: "aws_secrets_manager",
        region: "us-east-1",
        enabled: true,
      },
    ],
    isLoading: false,
    error: null,
  }),
  useAwsSecretReferences: () => ({
    createReference: mockCreateReference,
    updateReference: jest.fn(),
  }),
}))

describe("AwsSecretReferenceForm", () => {
  it("explains a full SECRETS reference typed into the name field", async () => {
    render(<AwsSecretReferenceForm onSaved={jest.fn()} />)

    fireEvent.change(screen.getByPlaceholderText("hello_world"), {
      target: { value: "SECRETS.hello_world.hello" },
    })
    fireEvent.click(screen.getByRole("button", { name: /Save reference/ }))

    expect(
      await screen.findByText(
        "Enter only the name, e.g. hello_world, not the full SECRETS.<name>.<key> reference."
      )
    ).toBeInTheDocument()
    expect(mockCreateReference).not.toHaveBeenCalled()
  })

  it("previews the reference for a valid name", () => {
    render(<AwsSecretReferenceForm onSaved={jest.fn()} />)

    fireEvent.change(screen.getByPlaceholderText("hello_world"), {
      target: { value: "hello_world" },
    })

    expect(screen.getByText("SECRETS.hello_world.<key>")).toBeInTheDocument()
  })
})

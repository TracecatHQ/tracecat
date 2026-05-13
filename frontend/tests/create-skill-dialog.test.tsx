import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { CreateSkillDialog } from "@/components/skills/create-skill-dialog"

const SKILL_NAME_REQUIRED_ERROR = "Name is required."
const SKILL_NAME_TRAILING_HYPHEN_ERROR = "Name cannot end with a hyphen."

jest.mock("@/components/ui/dialog", () => ({
  Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
    open ? <div>{children}</div> : null,
  DialogContent: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogDescription: ({ children }: { children: ReactNode }) => (
    <p>{children}</p>
  ),
  DialogFooter: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogHeader: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  DialogTitle: ({ children }: { children: ReactNode }) => <h2>{children}</h2>,
}))

describe("CreateSkillDialog", () => {
  it("preserves accented Latin letters when sanitizing skill names", () => {
    const onNameChange = jest.fn()

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name=""
        onNameChange={onNameChange}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={jest.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Café Tools" },
    })

    expect(onNameChange).toHaveBeenCalledWith("cafe-tools")
  })

  it("preserves a trailing hyphen while the user composes a name", () => {
    const onNameChange = jest.fn()

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name=""
        onNameChange={onNameChange}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={jest.fn()}
      />
    )

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "threat-" },
    })

    expect(onNameChange).toHaveBeenCalledWith("threat-")
  })

  it("defers skill name validation until create is attempted", () => {
    const onCreate = jest.fn(async () => {})

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name="threat-"
        onNameChange={jest.fn()}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={onCreate}
      />
    )

    const nameInput = screen.getByLabelText("Name")
    expect(nameInput).not.toHaveAttribute("aria-invalid")
    expect(
      screen.queryByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
    ).not.toBeInTheDocument()

    const createButton = screen.getByRole("button", { name: "Create skill" })
    expect(createButton).toBeEnabled()

    fireEvent.click(createButton)

    expect(onCreate).not.toHaveBeenCalled()
    expect(nameInput).toHaveAttribute("aria-invalid", "true")
    expect(
      screen.getByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
    ).toBeInTheDocument()
  })

  it("shows required name validation only after create is attempted", () => {
    const onCreate = jest.fn(async () => {})

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name=""
        onNameChange={jest.fn()}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={onCreate}
      />
    )

    const nameInput = screen.getByLabelText("Name")
    expect(nameInput).not.toHaveAttribute("aria-invalid")
    expect(
      screen.queryByText(SKILL_NAME_REQUIRED_ERROR)
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    expect(onCreate).not.toHaveBeenCalled()
    expect(nameInput).toHaveAttribute("aria-invalid", "true")
    expect(screen.getByText(SKILL_NAME_REQUIRED_ERROR)).toBeInTheDocument()
  })

  it("clears a submitted skill name validation error when editing resumes", () => {
    const onNameChange = jest.fn()

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name="threat-"
        onNameChange={onNameChange}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={jest.fn(async () => {})}
      />
    )

    const nameInput = screen.getByLabelText("Name")
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    expect(nameInput).toHaveAttribute("aria-invalid", "true")

    fireEvent.change(nameInput, {
      target: { value: "threat-intel" },
    })

    expect(onNameChange).toHaveBeenCalledWith("threat-intel")
    expect(nameInput).not.toHaveAttribute("aria-invalid")
    expect(
      screen.queryByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
    ).not.toBeInTheDocument()
  })

  it("submits when the skill name is valid", () => {
    const onCreate = jest.fn(async () => {})

    render(
      <CreateSkillDialog
        open={true}
        onOpenChange={jest.fn()}
        name="threat-intel"
        onNameChange={jest.fn()}
        description=""
        onDescriptionChange={jest.fn()}
        pending={false}
        onCreate={onCreate}
      />
    )

    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    expect(onCreate).toHaveBeenCalledTimes(1)
  })
})

import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import type { ComponentProps, ReactNode } from "react"
import {
  CreateSkillDialog,
  type CreateSkillDialogValues,
} from "@/components/skills/create-skill-dialog"

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

function renderCreateSkillDialog(
  props: Partial<ComponentProps<typeof CreateSkillDialog>> = {}
) {
  const defaults = {
    open: true,
    onOpenChange: jest.fn(),
    pending: false,
    onCreate: jest.fn<Promise<void>, [CreateSkillDialogValues]>(async () => {}),
  }

  render(<CreateSkillDialog {...defaults} {...props} />)

  return defaults
}

describe("CreateSkillDialog", () => {
  it("preserves accented Latin letters when sanitizing skill names", () => {
    renderCreateSkillDialog()

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "Café Tools" },
    })

    expect(screen.getByLabelText("Name")).toHaveValue("cafe-tools")
  })

  it("preserves a trailing hyphen while the user composes a name", () => {
    renderCreateSkillDialog()

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "threat-" },
    })

    expect(screen.getByLabelText("Name")).toHaveValue("threat-")
  })

  it("defers skill name validation until create is attempted", async () => {
    const { onCreate } = renderCreateSkillDialog()

    const nameInput = screen.getByLabelText("Name")
    fireEvent.change(nameInput, {
      target: { value: "threat-" },
    })

    expect(nameInput).not.toHaveAttribute("aria-invalid", "true")
    expect(
      screen.queryByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
    ).not.toBeInTheDocument()

    const createButton = screen.getByRole("button", { name: "Create skill" })
    expect(createButton).toBeEnabled()

    fireEvent.click(createButton)

    await waitFor(() => {
      expect(onCreate).not.toHaveBeenCalled()
      expect(nameInput).toHaveAttribute("aria-invalid", "true")
      expect(
        screen.getByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
      ).toBeInTheDocument()
    })
  })

  it("shows required name validation only after create is attempted", async () => {
    const { onCreate } = renderCreateSkillDialog()

    const nameInput = screen.getByLabelText("Name")
    expect(nameInput).not.toHaveAttribute("aria-invalid", "true")
    expect(
      screen.queryByText(SKILL_NAME_REQUIRED_ERROR)
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    await waitFor(() => {
      expect(onCreate).not.toHaveBeenCalled()
      expect(nameInput).toHaveAttribute("aria-invalid", "true")
      expect(screen.getByText(SKILL_NAME_REQUIRED_ERROR)).toBeInTheDocument()
    })
  })

  it("clears a submitted skill name validation error when editing resumes", async () => {
    renderCreateSkillDialog()

    const nameInput = screen.getByLabelText("Name")
    fireEvent.change(nameInput, {
      target: { value: "threat-" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    await waitFor(() => {
      expect(nameInput).toHaveAttribute("aria-invalid", "true")
    })

    fireEvent.change(nameInput, {
      target: { value: "threat-intel" },
    })

    expect(nameInput).toHaveValue("threat-intel")
    expect(nameInput).not.toHaveAttribute("aria-invalid", "true")
    expect(
      screen.queryByText(SKILL_NAME_TRAILING_HYPHEN_ERROR)
    ).not.toBeInTheDocument()
  })

  it("submits when the skill name is valid", async () => {
    const { onCreate } = renderCreateSkillDialog()

    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "threat-intel" },
    })
    fireEvent.change(screen.getByLabelText("Description"), {
      target: { value: "Optional details" },
    })
    fireEvent.click(screen.getByRole("button", { name: "Create skill" }))

    await waitFor(() => {
      expect(onCreate).toHaveBeenCalledTimes(1)
      expect(onCreate).toHaveBeenCalledWith({
        name: "threat-intel",
        description: "Optional details",
      })
    })
  })
})

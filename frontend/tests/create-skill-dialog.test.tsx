import { fireEvent, render, screen } from "@testing-library/react"
import type { ReactNode } from "react"
import { CreateSkillDialog } from "@/components/skills/create-skill-dialog"

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
})

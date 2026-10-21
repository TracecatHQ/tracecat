import { render, screen } from "@testing-library/react"
import { SkillLibraryMaintainerMark } from "@/components/skills/skill-library-provider-icon"
import { TooltipProvider } from "@/components/ui/tooltip"

function renderMark(provider: string | null | undefined) {
  return render(
    <TooltipProvider>
      <SkillLibraryMaintainerMark provider={provider} />
    </TooltipProvider>
  )
}

describe("SkillLibraryMaintainerMark", () => {
  it.each([undefined, null])(
    "renders nothing for official skills (%s)",
    (provider) => {
      const { container } = renderMark(provider)
      expect(container).toBeEmptyDOMElement()
    }
  )

  it.each(["Tracecat", "Someone"])(
    "labels a %s-maintained skill",
    (provider) => {
      renderMark(provider)
      expect(
        screen.getByRole("button", { name: `Maintained by ${provider}` })
      ).toBeInTheDocument()
    }
  )
})

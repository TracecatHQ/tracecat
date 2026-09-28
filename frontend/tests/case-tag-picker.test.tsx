/**
 * @jest-environment jsdom
 */

import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import type { CaseTagRead } from "@/client"
import { CaseTagPicker } from "@/components/cases/case-tag-picker"
import { useAddCaseTag, useCaseTagCatalog, useRemoveCaseTag } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  useCaseTagCatalog: jest.fn(),
  useAddCaseTag: jest.fn(),
  useRemoveCaseTag: jest.fn(),
}))

jest.mock("@/components/ui/use-toast", () => ({
  useToast: () => ({ toast: jest.fn() }),
}))

const mockUseCaseTagCatalog = useCaseTagCatalog as jest.MockedFunction<
  typeof useCaseTagCatalog
>
const mockUseAddCaseTag = useAddCaseTag as jest.MockedFunction<
  typeof useAddCaseTag
>
const mockUseRemoveCaseTag = useRemoveCaseTag as jest.MockedFunction<
  typeof useRemoveCaseTag
>

function makeTag(id: string, name: string): CaseTagRead {
  return { id, name, ref: name, color: "#aabbcc" } as CaseTagRead
}

const catalog = [makeTag("tag-1", "alpha"), makeTag("tag-2", "beta")]

function setup({
  addCaseTag = jest.fn().mockResolvedValue(undefined),
  removeCaseTag = jest.fn().mockResolvedValue(undefined),
  appliedTags = [catalog[0]!],
}: {
  addCaseTag?: jest.Mock
  removeCaseTag?: jest.Mock
  appliedTags?: CaseTagRead[]
} = {}) {
  mockUseCaseTagCatalog.mockReturnValue({
    caseTags: catalog,
  } as unknown as ReturnType<typeof useCaseTagCatalog>)
  mockUseAddCaseTag.mockReturnValue({
    addCaseTag,
  } as unknown as ReturnType<typeof useAddCaseTag>)
  mockUseRemoveCaseTag.mockReturnValue({
    removeCaseTag,
  } as unknown as ReturnType<typeof useRemoveCaseTag>)

  render(
    <CaseTagPicker
      caseId="case-1"
      workspaceId="ws-1"
      appliedTags={appliedTags}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: "Manage tags" }))
  return { addCaseTag, removeCaseTag }
}

/** The option row for a tag, found by its visible name. */
async function findTagOption(name: RegExp) {
  return await screen.findByRole("option", { name })
}

describe("CaseTagPicker", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("marks a tag as applied immediately, before the mutation lands", async () => {
    // A never-resolving add keeps the mutation in flight for the whole test.
    const addCaseTag = jest.fn().mockReturnValue(new Promise(() => {}))
    setup({ addCaseTag })

    const beta = await findTagOption(/beta/)
    expect(within(beta).queryByText(", applied")).not.toBeInTheDocument()

    fireEvent.click(beta)

    expect(addCaseTag).toHaveBeenCalledWith({ tag_id: "tag-2" })
    expect(within(beta).getByText(", applied")).toBeInTheDocument()
  })

  it("reverts the applied state when the mutation fails", async () => {
    const addCaseTag = jest.fn().mockRejectedValue(new Error("boom"))
    jest.spyOn(console, "error").mockImplementation(() => {})
    setup({ addCaseTag })

    const beta = await findTagOption(/beta/)
    fireEvent.click(beta)
    expect(within(beta).getByText(", applied")).toBeInTheDocument()

    await waitFor(() =>
      expect(within(beta).queryByText(", applied")).not.toBeInTheDocument()
    )
  })

  it("issues add then remove for two quick toggles of the same tag", async () => {
    // The add stays pending across the second click: the picker must read its
    // own optimistic state, not the stale server data, to pick the operation.
    const addCaseTag = jest.fn().mockReturnValue(new Promise(() => {}))
    const removeCaseTag = jest.fn().mockReturnValue(new Promise(() => {}))
    setup({ addCaseTag, removeCaseTag })

    const beta = await findTagOption(/beta/)
    fireEvent.click(beta)
    fireEvent.click(beta)

    expect(addCaseTag).toHaveBeenCalledTimes(1)
    expect(removeCaseTag).toHaveBeenCalledTimes(1)
    expect(removeCaseTag).toHaveBeenCalledWith("tag-2")
  })

  it("removes an already-applied tag", async () => {
    const removeCaseTag = jest.fn().mockResolvedValue(undefined)
    const { addCaseTag } = setup({ removeCaseTag })

    const alpha = await findTagOption(/alpha/)
    expect(within(alpha).getByText(", applied")).toBeInTheDocument()

    fireEvent.click(alpha)

    expect(removeCaseTag).toHaveBeenCalledWith("tag-1")
    expect(addCaseTag).not.toHaveBeenCalled()
    expect(within(alpha).queryByText(", applied")).not.toBeInTheDocument()
  })
})

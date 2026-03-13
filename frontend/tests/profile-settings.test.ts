import { getProfileNameUpdate } from "@/components/settings/profile-settings"

describe("profile settings", () => {
  it("returns null when the normalized name is unchanged", () => {
    expect(getProfileNameUpdate("Jane Doe", "  Jane   Doe  ")).toBeNull()
  })

  it("clears first and last name when the input is emptied", () => {
    expect(getProfileNameUpdate("Jane Doe", "   ")).toEqual({
      first_name: null,
      last_name: null,
    })
  })

  it("splits a new display name into first and last name parts", () => {
    expect(getProfileNameUpdate("Jane Doe", "  Ada Lovelace  ")).toEqual({
      first_name: "Ada",
      last_name: "Lovelace",
    })
  })
})

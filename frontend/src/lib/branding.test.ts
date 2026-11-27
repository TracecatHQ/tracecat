import {
  buildBrandingCss,
  foregroundHslTriplet,
  hexToHslTriplet,
} from "@/lib/branding"

describe("branding colors", () => {
  it("converts hex to an HSL triplet", () => {
    expect(hexToHslTriplet("#6E7ED8")).toBe("231 58% 64%")
    expect(hexToHslTriplet("#000000")).toBe("0 0% 0%")
  })

  it("picks readable foreground text", () => {
    expect(foregroundHslTriplet("#111111")).toBe("0 0% 100%")
    expect(foregroundHslTriplet("#FDE68A")).toBe("0 0% 9%")
  })

  it("scopes light and dark overrides separately", () => {
    const css = buildBrandingCss({
      primary_color_light: "#5E6AD2",
      primary_color_dark: "#8B93FF",
    })
    expect(css).toContain(":root:not(.dark) {")
    expect(css).toContain(".dark {")
  })

  it("ignores missing and invalid colors", () => {
    expect(buildBrandingCss(null)).toBe("")
    expect(
      buildBrandingCss({
        primary_color_light: "red; } body { display: none",
        primary_color_dark: null,
      })
    ).toBe("")
  })
})

import type { BrandingRead } from "@/client"

/** Pattern for the `#RRGGBB` colors accepted by organization branding. */
export const HEX_COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/

/** Default Tracecat primary color, matching `--primary` in globals.scss. */
export const DEFAULT_PRIMARY_COLOR = "#6E7ED8"

/** One-liner shown beside the Tracecat logo on the sign-in pages. */

function hexToRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16)
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255]
}

/**
 * Converts `#RRGGBB` to the `H S% L%` triplet the CSS theme variables use.
 */
export function hexToHslTriplet(hex: string): string {
  const [r, g, b] = hexToRgb(hex).map((channel) => channel / 255)
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const lightness = (max + min) / 2
  const delta = max - min
  let hue = 0
  let saturation = 0
  if (delta !== 0) {
    saturation = delta / (1 - Math.abs(2 * lightness - 1))
    if (max === r) {
      hue = ((g - b) / delta) % 6
    } else if (max === g) {
      hue = (b - r) / delta + 2
    } else {
      hue = (r - g) / delta + 4
    }
    hue *= 60
    if (hue < 0) hue += 360
  }
  return `${Math.round(hue)} ${Math.round(saturation * 100)}% ${Math.round(lightness * 100)}%`
}

/**
 * Picks white or near-black text for a background color using WCAG
 * relative luminance.
 */
export function foregroundHslTriplet(hex: string): string {
  const [r, g, b] = hexToRgb(hex).map((channel) => {
    const c = channel / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  })
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
  return luminance > 0.4 ? "0 0% 9%" : "0 0% 100%"
}

function primaryVariables(hex: string | null | undefined): string | null {
  if (!hex || !HEX_COLOR_PATTERN.test(hex)) {
    return null
  }
  return `--primary: ${hexToHslTriplet(hex)}; --primary-foreground: ${foregroundHslTriplet(hex)}; --ring: ${hexToHslTriplet(hex)};`
}

/**
 * Builds the CSS that overrides the theme's primary color for an
 * organization. Returns an empty string when nothing is customized.
 */
export function buildBrandingCss(
  branding: Pick<
    BrandingRead,
    "primary_color_light" | "primary_color_dark"
  > | null
): string {
  if (!branding) {
    return ""
  }
  const rules: string[] = []
  const light = primaryVariables(branding.primary_color_light)
  if (light) {
    rules.push(`:root:not(.dark) { ${light} }`)
  }
  const dark = primaryVariables(branding.primary_color_dark)
  if (dark) {
    rules.push(`.dark { ${dark} }`)
  }
  return rules.join("\n")
}

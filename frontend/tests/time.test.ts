import {
  durationBetween,
  durationToHumanReadable,
  formatDurationCompact,
  formatDurationLong,
  formatIntervalCompact,
  formatISODurationCompact,
  parseISODuration,
  parseISODurationSafe,
} from "@/lib/time"

describe("time helpers", () => {
  it("parses ISO durations with fractional seconds", () => {
    expect(parseISODuration("P1DT4H4M10.01724S")).toEqual({
      years: 0,
      months: 0,
      weeks: 0,
      days: 1,
      hours: 4,
      minutes: 4,
      seconds: 10,
    })
  })

  it("renders fractional-second durations without microseconds", () => {
    expect(durationToHumanReadable("PT1M30.4S")).toBe("1 minute, 30 seconds")
  })
})

describe("formatDurationCompact", () => {
  it("renders a single unit when only one is present", () => {
    expect(formatDurationCompact({ seconds: 25 })).toBe("25s")
  })

  it("renders the two most significant units", () => {
    expect(formatDurationCompact({ minutes: 15, seconds: 17 })).toBe("15m 17s")
    expect(formatDurationCompact({ hours: 5, minutes: 15, seconds: 17 })).toBe(
      "5h 15m"
    )
    expect(
      formatDurationCompact({ days: 1, hours: 5, minutes: 15, seconds: 17 })
    ).toBe("1d 5h")
    expect(formatDurationCompact({ days: 99, hours: 23, minutes: 59 })).toBe(
      "99d 23h"
    )
  })

  it("renders 0s for empty and all-zero durations", () => {
    expect(formatDurationCompact({})).toBe("0s")
    expect(
      formatDurationCompact({
        years: 0,
        months: 0,
        weeks: 0,
        days: 0,
        hours: 0,
        minutes: 0,
        seconds: 0,
      })
    ).toBe("0s")
  })

  it("takes contiguous units, not the next non-zero unit", () => {
    expect(formatDurationCompact({ days: 1, minutes: 59 })).toBe("1d")
    expect(formatDurationCompact({ years: 2, seconds: 30 })).toBe("2y")
  })

  it("truncates rather than rounds", () => {
    expect(formatDurationCompact({ hours: 5, minutes: 59, seconds: 59 })).toBe(
      "5h 59m"
    )
    expect(formatDurationCompact({ minutes: 59, seconds: 59 })).toBe("59m 59s")
  })

  it("honours maxUnits", () => {
    const duration = { days: 1, hours: 5, minutes: 15, seconds: 17 }
    expect(formatDurationCompact(duration, { maxUnits: 1 })).toBe("1d")
    expect(formatDurationCompact(duration, { maxUnits: 3 })).toBe("1d 5h 15m")
    expect(formatDurationCompact({ seconds: 25 }, { maxUnits: 1 })).toBe("25s")
  })

  it("clamps out-of-range maxUnits instead of rendering nothing", () => {
    const duration = { days: 1, hours: 5, minutes: 15, seconds: 17 }
    expect(formatDurationCompact(duration, { maxUnits: 0 })).toBe("1d")
    expect(formatDurationCompact(duration, { maxUnits: 99 })).toBe(
      "1d 5h 15m 17s"
    )
    expect(formatDurationCompact(duration, { maxUnits: Number.NaN })).toBe(
      "1d 5h"
    )
  })

  it("folds weeks into days", () => {
    expect(formatDurationCompact({ weeks: 2 })).toBe("14d")
    expect(formatDurationCompact({ weeks: 1, days: 3, hours: 4 })).toBe(
      "10d 4h"
    )
  })

  it("does not let the folded-away weeks slot swallow days", () => {
    expect(formatDurationCompact({ months: 2, days: 20 })).toBe("2mo 20d")
    expect(formatDurationCompact({ months: 2, days: 3, hours: 4 })).toBe(
      "2mo 3d"
    )
    expect(formatDurationCompact({ months: 1, weeks: 2, days: 1 })).toBe(
      "1mo 15d"
    )
  })

  // Pins the invariant behind SLOW_TICK_THRESHOLD_MS in case-duration-metrics.tsx:
  // at the default two-unit window, anything past an hour stops rendering
  // seconds, so the ticker may safely drop to a 30s interval there. Widening the
  // default window without revisiting that threshold would strand a visible
  // seconds field on a 30s tick.
  it("stops rendering seconds at an hour, which the tick tier depends on", () => {
    expect(formatDurationCompact({ minutes: 59, seconds: 59 })).toBe("59m 59s")
    expect(formatDurationCompact({ hours: 1, minutes: 5, seconds: 30 })).toBe(
      "1h 5m"
    )
  })
})

describe("formatDurationLong", () => {
  it("renders every non-zero unit, pluralised", () => {
    expect(formatDurationLong({ days: 1, hours: 5, minutes: 15 })).toBe(
      "1 day, 5 hours, 15 minutes"
    )
    expect(formatDurationLong({ seconds: 1 })).toBe("1 second")
  })

  it("renders 0 seconds for an empty duration", () => {
    expect(formatDurationLong({})).toBe("0 seconds")
  })

  it("keeps weeks, which schedule intervals rely on", () => {
    expect(formatDurationLong({ weeks: 2 })).toBe("2 weeks")
  })
})

describe("parseISODurationSafe", () => {
  it("parses valid input", () => {
    expect(parseISODurationSafe("PT25S")?.seconds).toBe(25)
  })

  it("returns null for missing or unparseable input without throwing", () => {
    expect(parseISODurationSafe(null)).toBeNull()
    expect(parseISODurationSafe(undefined)).toBeNull()
    expect(parseISODurationSafe("")).toBeNull()
    expect(parseISODurationSafe("not-a-duration")).toBeNull()
    // Negative timedeltas are constructible through the API and serialise with
    // a leading "-P", which the parser rejects rather than logs.
    expect(parseISODurationSafe("-PT30S")).toBeNull()
  })
})

describe("formatISODurationCompact", () => {
  it("formats parseable ISO durations", () => {
    expect(formatISODurationCompact("P1DT5H15M17S")).toBe("1d 5h")
    expect(formatISODurationCompact("PT25S")).toBe("25s")
    expect(formatISODurationCompact("P1DT5H15M17S", { maxUnits: 1 })).toBe("1d")
  })

  it("returns null for missing or unparseable input", () => {
    expect(formatISODurationCompact(null)).toBeNull()
    expect(formatISODurationCompact(undefined)).toBeNull()
    expect(formatISODurationCompact("")).toBeNull()
    expect(formatISODurationCompact("not-a-duration")).toBeNull()
  })
})

describe("durationBetween", () => {
  const start = new Date("2026-01-01T00:00:00.000Z")

  it("returns an empty duration when the interval is empty or inverted", () => {
    expect(durationBetween(start, start)).toEqual({})
    expect(
      durationBetween(start, new Date("2025-12-31T23:59:00.000Z"))
    ).toEqual({})
  })
})

describe("formatIntervalCompact", () => {
  const start = new Date("2026-01-01T00:00:00.000Z")

  it("returns 0s when the interval is empty or inverted", () => {
    expect(formatIntervalCompact(start, start)).toBe("0s")
    expect(
      formatIntervalCompact(start, new Date("2025-12-31T23:59:00.000Z"))
    ).toBe("0s")
  })

  it("formats elapsed time between two instants", () => {
    expect(
      formatIntervalCompact(start, new Date("2026-01-01T00:00:08.000Z"))
    ).toBe("8s")
    expect(
      formatIntervalCompact(start, new Date("2026-01-01T00:01:12.000Z"))
    ).toBe("1m 12s")
    expect(
      formatIntervalCompact(start, new Date("2026-01-01T02:05:30.000Z"))
    ).toBe("2h 5m")
    expect(
      formatIntervalCompact(start, new Date("2026-01-02T05:15:17.000Z"), {
        maxUnits: 4,
      })
    ).toBe("1d 5h 15m 17s")
  })

  // intervalToDuration is calendar-aware, so a real multi-month span exercises
  // variable month lengths rather than the synthetic literals above.
  it("decomposes a real multi-month interval", () => {
    expect(
      formatIntervalCompact(start, new Date("2026-03-21T00:00:00.000Z"))
    ).toBe("2mo 20d")
  })
})

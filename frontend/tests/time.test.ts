import { durationToHumanReadable, parseISODuration } from "@/lib/time"

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

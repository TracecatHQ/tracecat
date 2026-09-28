import { forwardClientAttributionHeaders } from "@/lib/forwarded-request-headers"

describe("forwardClientAttributionHeaders", () => {
  it("copies client attribution headers", () => {
    const source = new Headers({
      authorization: "Bearer synthetic-token",
      cookie: "session=synthetic",
      "user-agent": "SyntheticBrowser/1.0",
      "x-forwarded-for": "198.51.100.20",
    })
    const destination = new Headers({ cookie: "session=synthetic" })

    forwardClientAttributionHeaders(source, destination)

    expect(Object.fromEntries(destination.entries())).toEqual({
      cookie: "session=synthetic",
      "user-agent": "SyntheticBrowser/1.0",
      "x-forwarded-for": "198.51.100.20",
    })
  })

  it("leaves missing attribution headers unset", () => {
    const destination = new Headers({ "x-tracecat-role-type": "service" })

    forwardClientAttributionHeaders(new Headers(), destination)

    expect(Object.fromEntries(destination.entries())).toEqual({
      "x-tracecat-role-type": "service",
    })
  })
})

import { isServer, slugify, undoSlugify } from "@/lib/utils"

describe("slugify", () => {
  it("should convert a string to a slug", () => {
    const slug = slugify("Hello World")
    expect(slug).toBe("hello_world")
  })
})

describe("undoSlugify", () => {
  it("should convert a slug back to a string", () => {
    const string = undoSlugify("hello_world")
    expect(string).toBe("Hello World")
  })
})

describe("isServer", () => {
  it("should return true if the code is running on the server", () => {
    const result = isServer()
    expect(result).toBe(true)
  })
})

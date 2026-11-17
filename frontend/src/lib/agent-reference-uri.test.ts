import {
  parseReferenceURI,
  ReferenceURIError,
  serializeReferenceURI,
} from "@/lib/agent-reference-uri"
import fixtures from "../../../tests/fixtures/agent_references/conformance.json"

describe("reference URI conformance shared with Python", () => {
  for (const fixture of fixtures.uris) {
    it(fixture.name, () => {
      expect(fixture.error === null).not.toBe(fixture.canonical === null)
      if (fixture.error) {
        try {
          parseReferenceURI(fixture.uri)
          throw new Error("Expected URI rejection")
        } catch (error) {
          expect(error).toBeInstanceOf(ReferenceURIError)
          expect((error as ReferenceURIError).code).toBe(fixture.error)
        }
      } else {
        const target = parseReferenceURI(fixture.uri)
        expect(serializeReferenceURI(target)).toBe(fixture.canonical)
      }
    })
  }
})

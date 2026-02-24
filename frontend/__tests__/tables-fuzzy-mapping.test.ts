import {
  buildAutoColumnMapping,
  canonicalizeColumnName,
  resolveColumnMapping,
} from "@/lib/tables"

describe("CSV auto column mapping", () => {
  it("normalizes headers consistently", () => {
    expect(canonicalizeColumnName("\uFEFF Customer-Name ")).toBe(
      "customer_name"
    )
    expect(canonicalizeColumnName("freshservice id")).toBe("freshservice_id")
  })

  it("maps exact, canonical, and fuzzy matches for insert-rows CSV mapping", () => {
    const mapping = buildAutoColumnMapping(
      ["customer name", "freshservice-id", "pulse tenant id", "unknown metric"],
      ["customer_name", "freshservice_id", "pulse_tenant_id", "elastic_tag"]
    )

    expect(mapping["customer name"]).toBe("customer_name")
    expect(mapping["freshservice-id"]).toBe("freshservice_id")
    expect(mapping["pulse tenant id"]).toBe("pulse_tenant_id")
    expect(mapping["unknown metric"]).toBe("skip")
  })

  it("does not assign the same table column twice", () => {
    const mapping = buildAutoColumnMapping(
      ["customer_name", "customer name"],
      ["customer_name", "customer_id"]
    )

    expect(mapping.customer_name).toBe("customer_name")
    expect(mapping["customer name"]).toBe("skip")
  })

  it("repairs invalid existing mappings using fuzzy suggestions", () => {
    const mapping = resolveColumnMapping(
      ["customer_name", "freshservice_id", "pulse_tenant_id"],
      ["customer_name", "freshservice_id", "pulse_tenant_id"],
      {
        customer_name: "",
        freshservice_id: undefined,
        pulse_tenant_id: "not_a_real_column",
      }
    )

    expect(mapping.customer_name).toBe("customer_name")
    expect(mapping.freshservice_id).toBe("freshservice_id")
    expect(mapping.pulse_tenant_id).toBe("pulse_tenant_id")
  })

  it("preserves valid existing mappings", () => {
    const mapping = resolveColumnMapping(
      ["customer_name", "freshservice_id"],
      ["customer_name", "freshservice_id", "pulse_tenant_id"],
      {
        customer_name: "skip",
        freshservice_id: "freshservice_id",
      }
    )

    expect(mapping.customer_name).toBe("skip")
    expect(mapping.freshservice_id).toBe("freshservice_id")
  })
})

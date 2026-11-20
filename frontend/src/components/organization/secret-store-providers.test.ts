import type { SecretStoreRead } from "@/client"
import {
  SECRET_STORE_PROVIDERS,
  validateBitwardenOrganizationId,
  validateBitwardenSecretId,
  validateBitwardenServer,
} from "@/components/organization/secret-store-providers"

const ID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"

describe("Bitwarden provider entry", () => {
  it("accepts only lowercase secret IDs, as Bitwarden returns them", () => {
    expect(validateBitwardenSecretId(ID)).toBeNull()
    expect(validateBitwardenSecretId(ID.toUpperCase())).toMatch(/lowercase/)
    expect(validateBitwardenSecretId(ID.replaceAll("-", ""))).not.toBeNull()
    expect(
      SECRET_STORE_PROVIDERS.bitwarden_secrets_manager.reference.validate(ID)
    ).toBeNull()
  })

  it("accepts organization IDs in any case", () => {
    expect(validateBitwardenOrganizationId(ID.toUpperCase())).toBeNull()
    expect(validateBitwardenOrganizationId("not-an-id")).not.toBeNull()
  })

  it("accepts only a plain https base address for a self-hosted server", () => {
    const check = (server_url: string) =>
      validateBitwardenServer({ server_choice: "self_hosted", server_url })
    expect(check("https://vault.example.com")).toBeNull()
    expect(check("https://vault.example.com/bitwarden/")).toBeNull()
    for (const bad of [
      "http://vault.example.com",
      "https://vault.example.com/?x=1",
      "https://vault.example.com/#frag",
      "https://user:pass@vault.example.com",
      "https://exa mple.com",
      "https://internal-host\\x.example.com",
      "vault.example.com",
    ]) {
      expect(check(bad)).not.toBeNull()
    }
    expect(validateBitwardenServer({ server_choice: "eu" })).toBeNull()
  })

  it("sends the cloud region or the trimmed self-hosted address", () => {
    const entry = SECRET_STORE_PROVIDERS.bitwarden_secrets_manager
    expect(
      entry.toCreateConfig({ server_choice: "eu", organization_id: ID })
    ).toEqual({
      provider: "bitwarden_secrets_manager",
      server: "eu",
      organization_id: ID,
    })
    expect(
      entry.toCreateConfig({
        server_choice: "self_hosted",
        server_url: "https://vault.example.com/",
        organization_id: ID,
      })
    ).toMatchObject({ server: "https://vault.example.com" })
  })

  it("sends the access token on setup, and on edit only when replaced", () => {
    const entry = SECRET_STORE_PROVIDERS.bitwarden_secrets_manager
    const store = {
      provider: "bitwarden_secrets_manager",
      config: {
        provider: "bitwarden_secrets_manager",
        server: "eu",
        organization_id: ID,
      },
      access_token_set: true,
    } as SecretStoreRead

    expect(entry.validateSetup({ access_token: " " })).toHaveProperty(
      "access_token"
    )
    expect(entry.toSetupUpdate({ access_token: " token " })).toEqual({
      access_token: "token",
    })
    expect(entry.isSetupComplete(store)).toBe(true)
    expect(entry.isSetupComplete({ ...store, access_token_set: false })).toBe(
      false
    )

    // The edit form starts empty: the token is never read back.
    const draft = entry.editState(store)
    expect(draft).toMatchObject({ access_token: "", access_token_set: "true" })
    expect(entry.toUpdate(draft)).not.toHaveProperty("access_token")
    expect(entry.toUpdate({ ...draft, access_token: "new" })).toEqual({
      config: {
        provider: "bitwarden_secrets_manager",
        server: "eu",
        organization_id: ID,
      },
      access_token: "new",
    })
  })
})

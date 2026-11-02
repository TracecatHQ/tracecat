import {
  ApiError,
  authBeginAuthentication,
  authBeginRegistration,
  authCompleteAuthentication,
  authCompleteRegistration,
  authGetMfaStatus,
  type MfaStatusRead,
  type SessionMfaState,
  type WebAuthnRegistrationResult,
} from "@/client"

type Json = Record<string, unknown>

export const MFA_STATUS_QUERY_KEY = ["auth", "mfa-status"]

export function base64urlToBuffer(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/")
  const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")
  const binary = atob(padded)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}

export function bufferToBase64url(buffer: ArrayBuffer): string {
  let binary = ""
  for (const byte of new Uint8Array(buffer)) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "")
}

export function isWebAuthnSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential !== "undefined" &&
    typeof navigator.credentials !== "undefined"
  )
}

export function isMfaPending(state: SessionMfaState): boolean {
  return state === "challenge_required" || state === "enrollment_required"
}

/** Returns null when there is no session at all. */
export async function getMfaStatus(): Promise<MfaStatusRead | null> {
  try {
    return await authGetMfaStatus()
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      return null
    }
    throw error
  }
}

function toDescriptor(descriptor: Json): PublicKeyCredentialDescriptor {
  return {
    type: "public-key",
    id: base64urlToBuffer(descriptor.id as string),
    transports: descriptor.transports as AuthenticatorTransport[] | undefined,
  }
}

function toCreationOptions(options: Json): PublicKeyCredentialCreationOptions {
  const user = options.user as Json
  return {
    ...(options as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64urlToBuffer(options.challenge as string),
    user: {
      ...(user as unknown as PublicKeyCredentialUserEntity),
      id: base64urlToBuffer(user.id as string),
    },
    excludeCredentials: (
      (options.excludeCredentials as Json[] | undefined) ?? []
    ).map(toDescriptor),
  }
}

function toRequestOptions(options: Json): PublicKeyCredentialRequestOptions {
  return {
    ...(options as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64urlToBuffer(options.challenge as string),
    allowCredentials: (
      (options.allowCredentials as Json[] | undefined) ?? []
    ).map(toDescriptor),
  }
}

function describeWebAuthnError(error: unknown): Error {
  if (error instanceof DOMException) {
    if (error.name === "NotAllowedError") {
      return new Error("The security key request was cancelled or timed out.")
    }
    if (error.name === "InvalidStateError") {
      return new Error("This security key is already registered.")
    }
    if (error.name === "SecurityError") {
      return new Error(
        "Security keys need HTTPS and must be used on the Tracecat domain."
      )
    }
  }
  return error instanceof Error ? error : new Error(String(error))
}

/** Register a new security key or passkey for the signed-in user. */
export async function registerSecurityKey(
  name: string
): Promise<WebAuthnRegistrationResult> {
  const { public_key } = await authBeginRegistration()
  let credential: PublicKeyCredential | null
  try {
    credential = (await navigator.credentials.create({
      publicKey: toCreationOptions(public_key),
    })) as PublicKeyCredential | null
  } catch (error) {
    throw describeWebAuthnError(error)
  }
  if (!credential) {
    throw new Error("Security key registration was cancelled.")
  }
  const response = credential.response as AuthenticatorAttestationResponse
  return await authCompleteRegistration({
    requestBody: {
      name,
      credential: {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        type: credential.type,
        authenticatorAttachment:
          credential.authenticatorAttachment ?? undefined,
        response: {
          clientDataJSON: bufferToBase64url(response.clientDataJSON),
          attestationObject: bufferToBase64url(response.attestationObject),
          transports: response.getTransports?.() ?? [],
        },
        clientExtensionResults: credential.getClientExtensionResults(),
      },
    },
  })
}

/** Verify a registered security key for sign-in or step-up. */
export async function verifyWithSecurityKey(): Promise<MfaStatusRead> {
  const { public_key } = await authBeginAuthentication()
  let credential: PublicKeyCredential | null
  try {
    credential = (await navigator.credentials.get({
      publicKey: toRequestOptions(public_key),
    })) as PublicKeyCredential | null
  } catch (error) {
    throw describeWebAuthnError(error)
  }
  if (!credential) {
    throw new Error("Security key verification was cancelled.")
  }
  const response = credential.response as AuthenticatorAssertionResponse
  return await authCompleteAuthentication({
    requestBody: {
      credential: {
        id: credential.id,
        rawId: bufferToBase64url(credential.rawId),
        type: credential.type,
        authenticatorAttachment:
          credential.authenticatorAttachment ?? undefined,
        response: {
          clientDataJSON: bufferToBase64url(response.clientDataJSON),
          authenticatorData: bufferToBase64url(response.authenticatorData),
          signature: bufferToBase64url(response.signature),
          userHandle: response.userHandle
            ? bufferToBase64url(response.userHandle)
            : undefined,
        },
        clientExtensionResults: credential.getClientExtensionResults(),
      },
    },
  })
}

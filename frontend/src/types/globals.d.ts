export {}

declare global {
  interface CustomJwtSessionClaims {
    metadata: {
      onboardingComplete?: boolean
    }
  }

  interface Window {
    __ENV?: Record<string, string | undefined>
  }
}

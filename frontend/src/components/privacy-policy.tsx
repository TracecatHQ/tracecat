import Link from "next/link"

export default function PrivacyPolicy({ className }: { className?: string }) {
  return (
    <div className={className}>
      <p className="px-8 text-center text-xs text-muted-foreground">
        By using Tracecat, you agreeing to our{" "}
        <Link
          href="https://docs.google.com/document/d/e/2PACX-1vQvDe3SoVAPoQc51MgfGCP71IqFYX_rMVEde8zC4qmBCec5f8PLKQRdxa6tsUABT8gWAR9J-EVs2CrQ/pub"
          className="underline underline-offset-4 hover:text-primary"
        >
          Terms of Service
        </Link>{" "}
        and{" "}
        <Link
          href="https://docs.google.com/document/d/e/2PACX-1vTcdtgMyGfsi2yAVlwHthLJbz9kFfcwOiEaGJ6_qiyXo3QONr_qAYZDKmaK6yjptjYea14PZbLd01lF/pub"
          className="underline underline-offset-4 hover:text-primary"
        >
          Privacy Policy
        </Link>
        .
      </p>
    </div>
  )
}

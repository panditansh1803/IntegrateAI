import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "MAA — Manual Adjustments Agent",
  description:
    "ERP-driven journal entry validation dashboard. Review accepted, rejected, and quarantined manual adjustments with full audit traceability.",
  keywords: ["ERP", "journal entries", "validation", "audit", "finance", "agent"],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body>{children}</body>
    </html>
  );
}

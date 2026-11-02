import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Developer Profile Aggregator",
  description: "A self-hosted developer profile and coding statistics dashboard.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

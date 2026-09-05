import type { Metadata } from "next";
import "./globals.css";
import { Nav } from "@/components/Nav";

export const metadata: Metadata = {
  title: "RecoverAI",
  description: "AI Revenue Recovery Decision Engine",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-50 text-gray-900 antialiased">
        <div className="bg-amber-400 py-1.5 text-center text-xs font-semibold tracking-wide text-amber-950">
          SYNTHETIC DATA — this deployment runs on simulated payments, not real Razorpay data
        </div>
        <Nav />
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}

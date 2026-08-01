import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Nav } from "@/components/nav";
import { Footer } from "@/components/footer";

const sans = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({ subsets: ["latin"], variable: "--font-mono", display: "swap" });

export const metadata: Metadata = {
  title: {
    default: "AegisFlow — event-driven ML inference platform",
    template: "%s · AegisFlow",
  },
  description:
    "Durable ingest, an elastic worker fleet and chaos-verified fault tolerance. Submit inference, watch the queue, break the platform on purpose and read the recovery report.",
  keywords: [
    "distributed systems",
    "event driven architecture",
    "fault tolerance",
    "chaos engineering",
    "ML inference platform",
    "Kafka",
    "Kubernetes",
    "FastAPI",
  ],
  authors: [{ name: "Shahriar Ahmed Seam" }],
  openGraph: {
    title: "AegisFlow — event-driven ML inference platform",
    description:
      "Transactional outbox, effectively-once workers, circuit breakers, dead-letter replay and automated chaos drills that report recovery time and data loss.",
    type: "website",
  },
  robots: { index: true, follow: true },
  icons: { icon: [{ url: "/favicon.svg", type: "image/svg+xml" }] },
};

export const viewport: Viewport = {
  themeColor: "#06080B",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${sans.variable} ${mono.variable}`}>
      <body className="min-h-screen">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-signal focus:px-3 focus:py-2 focus:text-ink-950"
        >
          Skip to content
        </a>
        <Nav />
        <main id="main">{children}</main>
        <Footer />
      </body>
    </html>
  );
}

import type { Metadata } from "next";
import { Geist, Geist_Mono, Noto_Sans_Devanagari, Noto_Sans_Kannada, Noto_Sans_Tamil, Noto_Sans_Telugu } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const ta = Noto_Sans_Tamil({ variable: "--font-indic-ta", subsets: ["tamil"] });
const hi = Noto_Sans_Devanagari({ variable: "--font-indic-hi", subsets: ["devanagari"] });
const te = Noto_Sans_Telugu({ variable: "--font-indic-te", subsets: ["telugu"] });
const kn = Noto_Sans_Kannada({ variable: "--font-indic-kn", subsets: ["kannada"] });

export const metadata: Metadata = {
  title: "Indic Chaos Lab",
  description: "Adversarial reliability testing for Indian voice agents, built on Sarvam.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`dark ${geistSans.variable} ${geistMono.variable} ${ta.variable} ${hi.variable} ${te.variable} ${kn.variable} h-full antialiased`}>
      <body className="min-h-full">{children}</body>
    </html>
  );
}

import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {
  icons: { icon: "/transformx-mark.svg", shortcut: "/transformx-mark.svg" },
  title: "TransformX — Documents, thoughtfully transformed",
  description:
    "Translate your PDFs, redact sensitive information and review every result. Your document transformation workspace.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}

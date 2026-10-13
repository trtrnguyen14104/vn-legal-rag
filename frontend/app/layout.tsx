import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Pháp lý Việt — Tra cứu có căn cứ",
  description:
    "Tra cứu, giải thích và đối chiếu văn bản pháp luật Việt Nam với nguồn trích dẫn.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi">
      <body>{children}</body>
    </html>
  );
}

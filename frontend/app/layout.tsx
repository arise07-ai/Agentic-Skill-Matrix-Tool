import type { Metadata } from "next";
import AppFrame from "@/components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Skill Matrix",
  description: "Standardize skills, predict gaps, staff projects and build learning paths.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppFrame>{children}</AppFrame>
      </body>
    </html>
  );
}

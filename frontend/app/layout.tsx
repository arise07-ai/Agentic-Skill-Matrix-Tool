import type { Metadata } from "next";
import AppFrame from "@/components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Skill Matrix",
  description: "Standardize skills, predict gaps, staff projects and build learning paths.",
};

// Runs before the first paint so the saved theme and sidebar choice never flash the wrong way. No saved theme = follow the system.
const PREFERENCES = `try{var d=document.documentElement,t=localStorage.getItem("sm-theme");if(t==="light"||t==="dark")d.dataset.theme=t;if(localStorage.getItem("sm-nav")==="hidden")d.dataset.nav="hidden"}catch(e){}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: PREFERENCES }} />
      </head>
      <body>
        <AppFrame>{children}</AppFrame>
      </body>
    </html>
  );
}

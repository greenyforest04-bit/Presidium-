import type { Metadata, Viewport } from "next";
import "./globals.css";
import "./auth.css";
import "./stories.css";
import "./people.css";
import "./redesign.css";
import "./voice.css";
import "./capsule.css";
import "./agent.css";
import "./creation.css";
import {ViewportFrame} from "@/components/viewport-frame";
export const viewport:Viewport={width:"device-width",initialScale:1,viewportFit:"cover",themeColor:"#070e14"};

export const metadata: Metadata = {
  title: "Presidium — свои люди на связи",
  description: "Локальная альфа Presidium. Спасение в наших собственных руках.",
  icons: {
    icon: "/presidium-fist.png",
    shortcut: "/presidium-fist.png",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru" className="dark">
      <body className="antialiased"><ViewportFrame/>{children}</body>
    </html>
  );
}

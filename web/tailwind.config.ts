import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: {
          950: "#06080B",
          900: "#0A0D12",
          850: "#0E1218",
          800: "#141922",
          700: "#1C232E",
          600: "#2A333F",
        },
        signal: {
          DEFAULT: "#4FD1C5",
          soft: "#7FE3DA",
          deep: "#159C90",
        },
        amberline: "#F0B429",
        alarm: "#F2726D",
        chaos: "#B389F7",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
      },
      letterSpacing: {
        label: "0.16em",
      },
      boxShadow: {
        panel: "0 1px 0 0 rgba(255,255,255,0.04) inset, 0 24px 60px -32px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(79,209,197,0.35), 0 0 32px -8px rgba(79,209,197,0.45)",
      },
      backgroundImage: {
        hairlines:
          "linear-gradient(to right, rgba(255,255,255,0.045) 1px, transparent 1px), linear-gradient(to bottom, rgba(255,255,255,0.045) 1px, transparent 1px)",
      },
      keyframes: {
        pulseDot: {
          "0%, 100%": { opacity: "1", transform: "scale(1)" },
          "50%": { opacity: "0.45", transform: "scale(0.82)" },
        },
        travel: {
          "0%": { offsetDistance: "0%", opacity: "0" },
          "12%": { opacity: "1" },
          "88%": { opacity: "1" },
          "100%": { offsetDistance: "100%", opacity: "0" },
        },
        rise: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        sweep: {
          from: { transform: "translateX(-100%)" },
          to: { transform: "translateX(100%)" },
        },
      },
      animation: {
        "pulse-dot": "pulseDot 2.4s ease-in-out infinite",
        rise: "rise 0.5s ease-out both",
        sweep: "sweep 2.6s linear infinite",
      },
    },
  },
  plugins: [],
};

export default config;

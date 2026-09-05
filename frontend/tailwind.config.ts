import type { Config } from "tailwindcss";

// See docs/frontend/dashboard.md. Phase 6 ships the MVP dashboard,
// recovery-cases list, and case-detail views on top of this config.
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;

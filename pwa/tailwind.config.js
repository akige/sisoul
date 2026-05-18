/** @type {import('tailwindcss').Config} */
// sisoul PWA Tailwind config · 移动响应式 (sm/md/lg) + iPad/iPhone 适配
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx,js,jsx}"],
  theme: {
    extend: {
      colors: {
        // sisoul brand · 半匿名 OPSEC 风格 (深背景 + 单色高亮)
        sisoul: {
          bg: "#0b0d12",
          panel: "#141821",
          border: "#222836",
          text: "#e6e8ee",
          muted: "#8a92a6",
          accent: "#7c9cff",
          accentDim: "#3b4f8a",
          success: "#5ed29a",
          warn: "#f0b86d",
          danger: "#ef6b6b",
        },
      },
      screens: {
        // 默认 sm=640 md=768 lg=1024 xl=1280 2xl=1536
        // 加: iPhone 15 393 (默认 sm 之下), iPad Air 820 (md 之下), iPad Pro 1024 (lg)
        "ipad": "820px",
        "ipadpro": "1024px",
      },
      fontFamily: {
        mono: ["ui-monospace", "SF Mono", "Menlo", "Consolas", "monospace"],
        sans: [
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Helvetica Neue",
          "sans-serif",
        ],
      },
    },
  },
  plugins: [],
};

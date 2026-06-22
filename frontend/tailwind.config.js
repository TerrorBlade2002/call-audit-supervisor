/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] },
      colors: {
        ink: "hsl(222 22% 12%)",
        accent: "hsl(190 90% 42%)",
      },
    },
  },
  plugins: [],
};

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        gaming: {
          dark: '#0a0d14',
          card: '#111827',
          border: '#1f293d',
          accent: '#8b5cf6',
          cyan: '#06b6d4',
          green: '#10b981',
          danger: '#f43f5e'
        }
      }
    },
  },
  plugins: [],
}

import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Injects the main SoterCare web theme (colors, shadow-m) globally without modifying CSS files directly
const sotercareThemePlugin = () => {
  return {
    name: "sotercare-theme",
    transformIndexHtml(html) {
      return html.replace(
        "</head>",
        `  <style>
    :root {
      /* Main project structure themes */
      --bg: #fafafa;
      --surface: oklch(0.98 0 264);
      --border: rgba(0,0,0,0.05);
      --text-1: oklch(0.15 0 264);
      --text-2: oklch(0.4 0 264);
      --text-3: oklch(0.5 0 264);
      --shadow: inset 0 2px 0 #ffffff9d, inset 0 -1px 0 #adadad17, 0 -6px 8px -2px #00000008, 0 6px 8px -2px #00000012;
      --radius: 1.5rem;
      --radius-sm: 1rem;

      /* Mapping Dashboard colours to project palette */
      --teal: oklch(0.4 0 264);
      --teal-lt: rgba(0,0,0,0.03);
      --teal-dk: oklch(0.15 0 264);
      
      --coral: oklch(0.15 0 264);
      --coral-lt: rgba(0,0,0,0.02);
      
      --amber: oklch(0.4 0 264);
      --amber-lt: rgba(0,0,0,0.03);
      
      --green: oklch(0.4 0 264);
      --green-lt: rgba(0,0,0,0.02);
      
      --slate: oklch(0.15 0 264);
    }
    
    /* Set the outmost app bounds to true full screen */
    html, body, #root { 
      width: 100vw !important; 
      height: 100vh !important; 
      margin: 0 !important; 
      padding: 0 !important; 
      background-color: var(--bg) !important;
      overflow-x: hidden !important; 
    }
    
    /* Override the hardcoded 800px React wrapper container */
    #root > div {
      width: 100vw !important;
      height: 100vh !important;
      display: flex !important;
      flex-direction: column !important;
      align-items: center !important;
      background-color: var(--bg) !important;
      overflow-y: auto !important;
      overflow-x: hidden !important;
    }
    
    .topbar { 
      background-color: #ebf8ff !important; 
      border-bottom: none !important;
      width: 100vw !important;
      max-width: 100vw !important;
      margin: 0 !important;
      padding: 0 4rem !important; /* spacing for inside text so it doesn't crash sides */
      box-sizing: border-box !important;
      border-radius: 0 !important;
      align-self: flex-start !important;
      flex-shrink: 0 !important;
    }
    
    .main { 
      width: 100% !important;
      max-width: 800px !important; 
      margin: 0 auto !important; /* perfectly center the main dashboard cards */
      padding-top: 2rem !important; 
      height: auto !important;
    }
    
    .mcard, .timeline-panel { border: 1px solid rgba(0,0,0,0.05) !important; margin-bottom: 20px; }
  </style>\n</head>`
      );
    },
  };
};

export default defineConfig({
  plugins: [react(), sotercareThemePlugin()],
  server: {
    port: 5173,
    proxy: {
      "/socket.io": {
        target: "http://localhost:5000",
        ws: true,
        changeOrigin: true,
      },
      "/api": {
        target: "http://localhost:5000",
        changeOrigin: true,
      },
    },
  },
});

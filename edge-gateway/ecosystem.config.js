module.exports = {
  apps: [
    {
      name: "sotercare-gateway",
      script: "gateway_master.py",
      interpreter: "./.venv/bin/python",
      restart_delay: 2000,
      env: {
        // Required for espeak to play audio non-interactively
        XDG_RUNTIME_DIR: "/run/user/1000"
      }
    },
    {
      name: "sotercare-server",
      script: "server.py",
      interpreter: "./.venv/bin/python",
      restart_delay: 2000
    },
    {
      name: "sotercare-ui",
      script: "serve",
      env: {
        PM2_SERVE_PATH: "dashboard-ui/dist",
        PM2_SERVE_PORT: 5173,
        PM2_SERVE_SPA: "true",
        PM2_SERVE_HOMEPAGE: "/index.html"
      },
      restart_delay: 2000
    }
  ]
};

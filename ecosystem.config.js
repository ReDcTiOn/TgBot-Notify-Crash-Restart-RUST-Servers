// pm2: запуск бота из виртуального окружения .venv (Linux и Windows).
//   pm2 start ecosystem.config.js
const path = require("path");

const venvPython =
  process.platform === "win32"
    ? path.join(__dirname, ".venv", "Scripts", "python.exe")
    : path.join(__dirname, ".venv", "bin", "python");

module.exports = {
  apps: [
    {
      name: "FR-RustWatch",
      cwd: __dirname, // отсюда бот читает config.toml, .env и state.json
      script: venvPython,
      args: "-m rustwatch run",
      interpreter: "none", // script уже исполняемый python, pm2 не должен подставлять node
      autorestart: true,
      exp_backoff_restart_delay: 1000, // 1 с, затем растёт при повторных падениях
      kill_timeout: 15000, // после SIGINT бот успевает сохранить state.json и разослать очередь
      max_memory_restart: "300M",
      time: true, // метки времени в логах pm2
      env: {
        PYTHONUNBUFFERED: "1",
        PYTHONIOENCODING: "utf-8",
      },
    },
  ],
};

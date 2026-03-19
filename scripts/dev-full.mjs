import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const processes = [];

function loadDotEnv() {
    const envPath = path.join(process.cwd(), ".env");
    if (!fs.existsSync(envPath)) {
        return {};
    }

    const parsed = {};
    const content = fs.readFileSync(envPath, "utf-8");
    for (const line of content.split(/\r?\n/)) {
        const trimmed = line.trim();
        if (!trimmed || trimmed.startsWith("#")) continue;
        const eq = trimmed.indexOf("=");
        if (eq === -1) continue;
        let key = trimmed.slice(0, eq).trim();
        let value = trimmed.slice(eq + 1).trim();
        if (key.startsWith("export ")) {
            key = key.slice(7).trim();
        }
        if (
            (value.startsWith('"') && value.endsWith('"')) ||
            (value.startsWith("'") && value.endsWith("'"))
        ) {
            value = value.slice(1, -1);
        }
        parsed[key] = value;
    }

    return parsed;
}

const envFromFile = loadDotEnv();
const baseEnv = { ...envFromFile, ...process.env };

function resolveLitellmCommand() {
    const isWin = process.platform === "win32";
    const venvDir = path.join(process.cwd(), ".venv", isWin ? "Scripts" : "bin");
    const venvCommand = path.join(venvDir, isWin ? "litellm.exe" : "litellm");
    if (fs.existsSync(venvCommand)) {
        return venvCommand;
    }
    return "litellm";
}

function start(name, command, args) {
    const child = spawn(command, args, {
        cwd: process.cwd(),
        stdio: "inherit",
        env: baseEnv,
    });

    child.on("exit", (code, signal) => {
        if (signal) {
            console.log(`[dev:full] ${name} exited with signal ${signal}`);
        } else if (code && code !== 0) {
            console.error(`[dev:full] ${name} exited with code ${code}`);
            shutdown(code);
        }
    });

    child.on("error", (error) => {
        console.error(`[dev:full] failed to start ${name}: ${error.message}`);
        if (name === "litellm") {
            console.error("[dev:full] ensure the virtualenv exists and litellm is installed (npm run api:install)");
        }
        shutdown(1);
    });

    processes.push(child);
}

let shuttingDown = false;

function shutdown(exitCode = 0) {
    if (shuttingDown) return;
    shuttingDown = true;
    for (const child of processes) {
        if (!child.killed) {
            child.kill("SIGTERM");
        }
    }
    process.exit(exitCode);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

start("frontend", npmCommand, ["run", "dev"]);
start("backend", npmCommand, ["run", "api:dev"]);
start("litellm", resolveLitellmCommand(), [
    "--config",
    "infra/litellm/config.yaml",
    "--port",
    "4000",
]);

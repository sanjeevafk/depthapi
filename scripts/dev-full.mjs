import { spawn } from "node:child_process";

const npmCommand = process.platform === "win32" ? "npm.cmd" : "npm";
const processes = [];

function start(name, args) {
    const child = spawn(npmCommand, args, {
        cwd: process.cwd(),
        stdio: "inherit",
        env: process.env,
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

start("frontend", ["run", "dev"]);
start("backend", ["run", "api:dev"]);

import { randomBytes } from "node:crypto";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import net from "node:net";
import path from "node:path";


const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const webDirectory = path.resolve(scriptDirectory, "..");
const repositoryRoot = path.resolve(scriptDirectory, "..", "..", "..");
const composeFiles = ["-f", "compose.yml", "-f", "compose.fullstack-e2e.yml"];

function randomSecret() {
  return randomBytes(36).toString("base64url");
}

async function availablePort() {
  return await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close(() => reject(new Error("ephemeral port allocation failed")));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

async function run(command, args, options = {}) {
  await new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd ?? repositoryRoot,
      env: options.env,
      shell: false,
      stdio: "inherit",
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} exited with ${code ?? signal ?? "unknown status"}`));
    });
  });
}

const httpPort = await availablePort();
const httpsPort = await availablePort();
const baseUrl = `http://127.0.0.1:${httpPort}`;
const projectName = `rail-waitlist-e2e-${process.pid}-${randomBytes(4).toString("hex")}`;
const environment = {
  ...process.env,
  COMPOSE_PROJECT_NAME: projectName,
  COMPOSE_PROFILES: "experimental-rail",
  POSTGRES_DB: "rail_waitlist_e2e",
  POSTGRES_USER: "rail_waitlist_e2e",
  POSTGRES_PASSWORD: randomSecret(),
  SECRET_ENCRYPTION_KEY: randomSecret(),
  AUTH_SESSION_SECRET: randomSecret(),
  SRT_PROVIDER_ADAPTER_TOKEN: randomSecret(),
  E2E_BROWSER_TOKEN: randomSecret(),
  E2E_BASE_URL: baseUrl,
  CADDY_HTTP_BIND: `127.0.0.1:${httpPort}`,
  CADDY_HTTPS_BIND: `127.0.0.1:${httpsPort}`,
};
const dockerArgs = ["compose", ...composeFiles];
let failed = false;

try {
  await run("docker", [...dockerArgs, "config", "--quiet"], { env: environment });
  await run(
    "docker",
    [
      ...dockerArgs,
      "up",
      "-d",
      "--build",
      "--wait",
      "proxy",
      "e2e-fake-upstream",
      "e2e-korail-page",
      "korail-browser-adapter",
      "worker",
      "scheduler",
    ],
    { env: environment },
  );
  const playwrightCli = path.join(
    webDirectory,
    "node_modules",
    "@playwright",
    "test",
    "cli.js",
  );
  let browserFailure = null;
  try {
    await run(
      process.execPath,
      [
        playwrightCli,
        "test",
        "e2e/fullstack-journey.spec.ts",
        "--project=desktop-chromium",
        "--workers=1",
        "--retries=0",
      ],
      {
        cwd: webDirectory,
        env: {
          ...environment,
          RUN_FULLSTACK_E2E: "1",
        },
      },
    );
  } catch (error) {
    browserFailure = error;
  }
  let verifierFailure = null;
  try {
    await run(
      "docker",
      [
        ...dockerArgs,
        "exec",
        "-T",
        "api",
        "python",
        "/fullstack/assert_worker_state.py",
      ],
      { env: environment },
    );
  } catch (error) {
    verifierFailure = error;
  }
  if (browserFailure !== null) throw browserFailure;
  if (verifierFailure !== null) throw verifierFailure;
} catch (error) {
  failed = true;
  console.error(error instanceof Error ? error.message : String(error));
} finally {
  try {
    await run(
      "docker",
      [...dockerArgs, "down", "--volumes", "--remove-orphans", "--timeout", "10"],
      { env: environment },
    );
  } catch (cleanupError) {
    failed = true;
    console.error(
      cleanupError instanceof Error ? cleanupError.message : String(cleanupError),
    );
  }
}

if (failed) process.exitCode = 1;

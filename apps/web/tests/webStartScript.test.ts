import { spawnSync } from "node:child_process";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import vm from "node:vm";
import { afterEach, describe, expect, it } from "vitest";

const SCRIPT = readFileSync("docker/start.sh", "utf8");
const RUNTIME_DIRECTORY = "/tmp/railwait-runtime";
const workDirectories: string[] = [];

interface StartResult {
  status: number | null;
  stderr: string;
  nginxArguments: string;
  configScript: string;
  config: unknown;
}

// Runs the container start script with its output folder moved to a temporary directory and a
// stand-in nginx, so the test sees the generated file and the final exec without Docker.
function runStartScript(environment: Record<string, string>): StartResult {
  const directory = mkdtempSync(path.join(tmpdir(), "railwait-web-start-"));
  workDirectories.push(directory);
  const output = path.join(directory, "runtime");
  const bin = path.join(directory, "bin");
  const script = path.join(directory, "start.sh");
  const nginxLog = path.join(directory, "nginx-arguments");
  writeFileSync(script, SCRIPT.replaceAll(RUNTIME_DIRECTORY, output));
  spawnSync("mkdir", [bin]);
  writeFileSync(path.join(bin, "nginx"), "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$NGINX_LOG\"\n");
  chmodSync(path.join(bin, "nginx"), 0o755);
  const result = spawnSync("/bin/sh", [script], {
    encoding: "utf8",
    env: { PATH: bin + path.delimiter + "/usr/bin:/bin", NGINX_LOG: nginxLog, ...environment },
  });
  const configScript = readFileSync(path.join(output, "runtime-config.js"), "utf8");
  const context: { window: { __RAILWAIT_RUNTIME_CONFIG__?: unknown } } = { window: {} };
  vm.runInNewContext(configScript, context);
  return {
    status: result.status,
    stderr: result.stderr,
    nginxArguments: readFileSync(nginxLog, "utf8"),
    configScript,
    config: context.window.__RAILWAIT_RUNTIME_CONFIG__,
  };
}

describe.skipIf(process.platform === "win32")("web container start script", () => {
  afterEach(() => {
    for (const directory of workDirectories.splice(0)) rmSync(directory, { recursive: true });
  });

  it("writes the app-link settings from the environment and then runs nginx", () => {
    expect(SCRIPT).toContain(RUNTIME_DIRECTORY);
    const result = runStartScript({
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: "7.0.0+70000006",
      VITE_SRT_MAIN_DEEPLINK_ENABLED: "false",
      VITE_SRT_MAIN_VALIDATED_VERSION: "",
    });

    expect(result.status).toBe(0);
    expect(result.stderr).toBe("");
    expect(result.config).toEqual({
      VITE_KORAIL_BOOKING_DEEPLINK_ENABLED: "true",
      VITE_SRT_MAIN_DEEPLINK_ENABLED: "false",
      VITE_KORAIL_BOOKING_VALIDATED_VERSION: "7.0.0+70000006",
    });
    expect(result.nginxArguments).toBe("-g\ndaemon off;\n");
  });

  it("leaves out values that are not a switch or a short version", () => {
    const result = runStartScript({
      VITE_KORAIL_TICKET_DEEPLINK_ENABLED: "yes",
      VITE_KORAIL_TICKET_VALIDATED_VERSION: "7.0\"};alert(1)//",
      VITE_SRT_TICKET_VALIDATED_VERSION: "9".repeat(65),
    });

    expect(result.status).toBe(0);
    expect(result.configScript).toBe("window.__RAILWAIT_RUNTIME_CONFIG__ = {};\n");
    expect(result.stderr).toContain("ignored VITE_KORAIL_TICKET_DEEPLINK_ENABLED");
    expect(result.stderr).toContain("ignored VITE_KORAIL_TICKET_VALIDATED_VERSION");
    expect(result.stderr).toContain("ignored VITE_SRT_TICKET_VALIDATED_VERSION");
    expect(result.stderr).not.toContain("alert");
    expect(result.nginxArguments).toBe("-g\ndaemon off;\n");
  });
});

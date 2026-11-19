import type { InstallLocation } from "../../api/systemSettings";

export interface LabelledCommand {
  id: string;
  label: string;
  command: string;
}

/** Single-quote a value for POSIX shells (macOS Terminal, Linux), keeping embedded quotes. */
export function posixQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

/** Windows paths cannot contain double quotes, so double quoting is safe in PowerShell and cmd. */
export function windowsQuote(value: string): string {
  return `"${value}"`;
}

/**
 * Commands that open the install .env on the install computer. A browser page cannot open a
 * local folder itself, so the screen offers commands to copy instead. Unknown systems get the
 * railwait command alone, which picks the right file manager on any of them.
 */
export function installLocationCommands(location: InstallLocation): LabelledCommand[] {
  const railwait = {
    id: "railwait-open",
    label: "railwait CLI(1.5.0 이상)로 열기",
    command: `railwait open --path ${location.hostOs === "windows"
      ? windowsQuote(location.installPath)
      : posixQuote(location.installPath)}`,
  };
  switch (location.hostOs) {
    case "darwin":
      return [
        { id: "reveal", label: "Finder에서 .env 보기", command: `open -R ${posixQuote(location.envPath)}` },
        railwait,
      ];
    case "windows":
      return [
        { id: "folder", label: "파일 탐색기로 설치 폴더 열기", command: `explorer.exe ${windowsQuote(location.installPath)}` },
        { id: "edit", label: "메모장으로 .env 열기", command: `notepad.exe ${windowsQuote(location.envPath)}` },
        railwait,
      ];
    case "linux":
      return [
        { id: "folder", label: "파일 관리자로 설치 폴더 열기", command: `xdg-open ${posixQuote(location.installPath)}` },
        { id: "edit", label: "터미널에서 .env 고치기", command: `nano ${posixQuote(location.envPath)}` },
        railwait,
      ];
    case null:
      return [railwait];
  }
}

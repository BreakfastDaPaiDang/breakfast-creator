import { spawnSync } from "node:child_process";

const checks = [
  ["node", ["--version"]],
  ["npm", ["--version"]],
  ["python", ["--version"]],
  ["uv", ["--version"]],
  ["ffmpeg", ["-version"]],
  ["ffprobe", ["-version"]],
  ["yt-dlp", ["--version"]],
];

let failed = false;
for (const [command, args] of checks) {
  const result = spawnSync(command, args, { encoding: "utf8", shell: true });
  const output = `${result.stdout ?? ""}${result.stderr ?? ""}`.trim().split(/\r?\n/)[0];
  if (result.status === 0) {
    console.log(`OK      ${command.padEnd(9)} ${output}`);
  } else {
    failed = true;
    console.error(`MISSING ${command.padEnd(9)} ${output || "not found"}`);
  }
}

process.exitCode = failed ? 1 : 0;


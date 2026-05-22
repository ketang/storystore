#!/usr/bin/env node
// Minimal placeholder CLI for fixture exercises.
const cmd = process.argv[2];
if (cmd === "init") {
  console.log("initialized");
} else if (cmd === "build") {
  console.log("built");
} else {
  console.error("usage: cli {init|build}");
  process.exit(2);
}

/** Isolated manual probe bridge; never started by the application. */
import { createInterface } from "node:readline";
import { runTurn, ProtocolError } from "./kernel.js";
const lines = createInterface({ input: process.stdin })[Symbol.asyncIterator]();
const config = JSON.parse((await lines.next()).value!);
if (!config.sessionRoot) throw new Error("An isolated session root is required");
try {
  const result = await runTurn(config.input, async (method, params) => {
    process.stdout.write(JSON.stringify({ method, params }) + "\n");
    const response = JSON.parse((await lines.next()).value!);
    if (!response.ok) throw new ProtocolError("PROBE_HOST_ERROR", response.error);
    return response.data;
  }, AbortSignal.timeout(600_000), { sessionRoot: config.sessionRoot });
  process.stdout.write(JSON.stringify({ result }) + "\n");
} catch (error) {
  process.stdout.write(JSON.stringify({ error: error instanceof Error ? error.message : String(error) }) + "\n");
  process.exitCode = 1;
} finally {
  process.stdin.destroy();
}

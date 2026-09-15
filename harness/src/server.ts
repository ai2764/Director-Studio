import { createServer as httpServer, type IncomingMessage, type Server } from "node:http";
import { randomUUID, timingSafeEqual } from "node:crypto";
import { pathToFileURL } from "node:url";
import { runTurn, ProtocolError, type Host, type TurnInput } from "./kernel.js";

const BODY_LIMIT = 8 * 1024 * 1024;
// Python owns the configurable turn deadline (at most 2 hours). The sidecar's
// orphan guard must not expire before a legitimate long backend capability.
const ORPHAN_TIMEOUT = (2 * 60 * 60 + 10) * 1000;
const UUID =
  "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
async function body(req: IncomingMessage): Promise<any> {
  let size = 0;
  const chunks: Buffer[] = [];
  for await (const chunk of req) {
    size += chunk.length;
    if (size > BODY_LIMIT)
      throw new ProtocolError("BODY_LIMIT", "Request body exceeds 8 MiB");
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new ProtocolError("INVALID_BODY", "Expected JSON body");
  }
}
function inputOf(value: any): TurnInput {
  if (
    !value ||
    (value.session_id !== undefined && (typeof value.session_id !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(value.session_id))) ||
    (value.operation !== undefined && !["chat", "compact"].includes(value.operation)) ||
    (value.operation === "compact" && !value.session_id) ||
    typeof value.message !== "string" ||
    value.message.length > 1000000 ||
    !Array.isArray(value.history) ||
    value.history.length > 10000 ||
    value.history.some(
      (r: any) =>
        !r ||
        !["user", "assistant"].includes(r.role) ||
        typeof r.content !== "string",
    ) ||
    !Number.isInteger(value.context_window) ||
    value.context_window < 256 ||
    value.context_window > 2000000 ||
    !Number.isInteger(value.max_steps) ||
    value.max_steps < 1 ||
    value.max_steps > 100
  )
    throw new ProtocolError("INVALID_BODY", "Invalid turn input or bounds");
  return value;
}
type Runner = typeof runTurn;
interface Active {
  abort: AbortController;
  pending?: {
    id: string;
    resolve: (v: unknown) => void;
    reject: (e: unknown) => void;
  };
}

type ParentSchedule = (
  callback: () => void,
  milliseconds: number,
) => Pick<NodeJS.Timeout, "unref">;

function processIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error: any) {
    return error?.code === "EPERM";
  }
}

export function installParentGuard(
  server: Pick<Server, "close" | "closeAllConnections">,
  rawPid: string | undefined,
  schedule: ParentSchedule = setInterval,
  isAlive: (pid: number) => boolean = processIsAlive,
) {
  if (rawPid === undefined) return;
  const parentPid = Number(rawPid);
  if (!Number.isInteger(parentPid) || parentPid < 1)
    throw new Error("Invalid DS_HARNESS_PARENT_PID");
  let stopped = false;
  const timer = schedule(() => {
    if (stopped || isAlive(parentPid)) return;
    stopped = true;
    server.close();
    server.closeAllConnections();
  }, 1000);
  timer.unref();
}

export function createServer(token: string, runner: Runner = runTurn) {
  if (!token.trim()) throw new Error("DS_HARNESS_INTERNAL_TOKEN is required");
  const active = new Map<string, Active>();
  const expected = Buffer.from(`Bearer ${token}`);
  const server = httpServer(async (req, res) => {
    const json = (status: number, data: unknown) => {
      res.writeHead(status, { "Content-Type": "application/json" });
      res.end(JSON.stringify(data));
    };
    const supplied = Buffer.from(req.headers.authorization ?? "");
    if (
      supplied.length !== expected.length ||
      !timingSafeEqual(supplied, expected)
    ) {
      json(401, { error: "Unauthorized" });
      return;
    }
    const path = req.url ?? "";
    if (req.method === "GET" && path === "/health") {
      json(200, { ok: true, service: "director-studio-harness", protocol: 1, capabilities: ["native-sessions-v1", "context-envelope-v2"] });
      return;
    }
    const match = path.match(
      new RegExp(`^/turns/(${UUID})(?:/responses/(${UUID}))?$`),
    );
    if (!match) {
      json(404, { error: "Not found" });
      return;
    }
    const [, id, responseId] = match;
    try {
      if (req.method === "DELETE" && !responseId) {
        const turn = active.get(id);
        if (!turn) {
          json(404, { error: "Turn not found" });
          return;
        }
        turn.abort.abort(new ProtocolError("CANCELLED", "Turn cancelled"));
        res.writeHead(204);
        res.end();
        return;
      }
      if (req.method !== "POST") {
        json(405, { error: "Method not allowed" });
        return;
      }
      if (responseId) {
        const data = await body(req);
        const pending = active.get(id)?.pending;
        if (!pending || pending.id !== responseId) {
          json(409, { error: "No matching pending request" });
          return;
        }
        if (
          data?.ok !== true &&
          !(
            data?.ok === false &&
            typeof data.error?.code === "string" &&
            typeof data.error?.message === "string"
          )
        ) {
          json(400, { error: "Invalid response envelope" });
          return;
        }
        active.get(id)!.pending = undefined;
        res.writeHead(204);
        res.end();
        if (data.ok) pending.resolve(data.data);
        else
          pending.reject(
            new ProtocolError(
              data.error.code,
              data.error.message,
              data.error.retryable === true,
            ),
          );
        return;
      }
      const input = inputOf(await body(req));
      if (active.has(id)) {
        json(409, { error: "Turn already active" });
        return;
      }
      if (active.size >= 8) {
        json(429, { error: "Sidecar turn capacity reached" });
        return;
      }
      const turn: Active = { abort: new AbortController() };
      active.set(id, turn);
      const deadline = setTimeout(
        () =>
          turn.abort.abort(
            new ProtocolError("DEADLINE", "Turn deadline exceeded"),
          ),
        ORPHAN_TIMEOUT,
      );
      deadline.unref();
      const emit = (event: unknown) => {
        if (!res.destroyed) res.write(JSON.stringify(event) + "\n");
      };
      res.writeHead(200, {
        "Content-Type": "application/x-ndjson",
        "Cache-Control": "no-store",
      });
      res.flushHeaders();
      const disconnect = () =>
        turn.abort.abort(
          new ProtocolError("DISCONNECTED", "Host disconnected"),
        );
      res.on("close", disconnect);
      turn.abort.signal.addEventListener(
        "abort",
        () => {
          turn.pending?.reject(turn.abort.signal.reason);
          turn.pending = undefined;
        },
        { once: true },
      );
      const host: Host = (method, params) =>
        new Promise((resolve, reject) => {
          if (turn.abort.signal.aborted) {
            reject(turn.abort.signal.reason);
            return;
          }
          if (turn.pending) {
            reject(
              new ProtocolError(
                "PROTOCOL",
                "Only one host request may be outstanding",
              ),
            );
            return;
          }
          const requestId = randomUUID();
          const requestDeadline = setTimeout(
            () =>
              turn.abort.abort(
                new ProtocolError("HOST_TIMEOUT", "Host response deadline exceeded"),
              ),
            ORPHAN_TIMEOUT,
          );
          requestDeadline.unref();
          turn.pending = {
            id: requestId,
            resolve: (value) => {
              clearTimeout(requestDeadline);
              resolve(value);
            },
            reject: (error) => {
              clearTimeout(requestDeadline);
              reject(error);
            },
          };
          emit({ type: "request", id: requestId, method, params });
        });
      try {
        emit({ type: "status", text: "Running Harness turn" });
        const result = await runner(input, host, turn.abort.signal);
        emit({ type: "result", ...result });
      } catch (error) {
        emit({
          type: "error",
          code:
            error instanceof ProtocolError
              ? error.code
              : turn.abort.signal.aborted
                ? "CANCELLED"
                : "HARNESS_ERROR",
          message: error instanceof Error ? error.message : String(error),
        });
      } finally {
        clearTimeout(deadline);
        res.off("close", disconnect);
        turn.abort.abort();
        active.delete(id);
        res.end();
      }
    } catch (error) {
      if (!res.headersSent)
        json(
          error instanceof ProtocolError && error.code === "BODY_LIMIT"
            ? 413
            : 400,
          { error: error instanceof Error ? error.message : String(error) },
        );
      else res.end();
    }
  });
  server.requestTimeout = 30000;
  server.headersTimeout = 10000;
  server.on("close", () => {
    for (const turn of active.values())
      turn.abort.abort(new ProtocolError("SHUTDOWN", "Sidecar stopped"));
  });
  return server;
}
if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const port = Number(process.env.DS_HARNESS_PORT ?? 8791);
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error("Invalid DS_HARNESS_PORT");
  const server = createServer(process.env.DS_HARNESS_INTERNAL_TOKEN ?? "");
  installParentGuard(server, process.env.DS_HARNESS_PARENT_PID);
  server.listen(port, "127.0.0.1", () =>
    console.log(`Director Studio Harness listening on 127.0.0.1:${port}`),
  );
  const stop = () => {
    server.close();
    server.closeAllConnections();
  };
  process.on("SIGINT", stop);
  process.on("SIGTERM", stop);
}

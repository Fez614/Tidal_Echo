import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";

const root = path.resolve("web");
const port = Number(process.env.DEV_PORT || 4174);
const host = process.env.DEV_HOST || "0.0.0.0";
const relayOrigin = process.env.RELAY_ORIGIN || "http://127.0.0.1:3011";

const types = new Map([
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".webmanifest", "application/manifest+json; charset=utf-8"],
  [".png", "image/png"],
  [".webp", "image/webp"],
  [".mp3", "audio/mpeg"],
]);

const server = http.createServer(async (req, res) => {
  try {
    const requestUrl = new URL(req.url || "/", `http://127.0.0.1:${port}`);

    if (requestUrl.pathname.startsWith("/relay/")) {
      await proxyRelay(req, res);
      return;
    }

    const pathname = requestUrl.pathname === "/" ? "/index.html" : decodeURIComponent(requestUrl.pathname);
    const filePath = path.resolve(root, `.${pathname}`);
    if (!filePath.startsWith(root)) throw new Error("Forbidden");

    const data = await fs.readFile(filePath);
    res.writeHead(200, {
      "Content-Type": types.get(path.extname(filePath)) || "application/octet-stream",
      "Cache-Control": "no-store",
    });
    res.end(data);
  } catch (error) {
    res.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    res.end(error?.message || "Not found");
  }
});

async function proxyRelay(req, res) {
  const requestUrl = new URL(req.url || "/", "http://127.0.0.1");
  const relayPath = requestUrl.pathname.replace(/^\/relay/, "") || "/";
  const target = new URL(relayPath + requestUrl.search, relayOrigin);
  const headers = {};
  for (const key of ["authorization", "content-type", "accept"]) {
    if (req.headers[key]) headers[key] = req.headers[key];
  }
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = chunks.length ? Buffer.concat(chunks) : undefined;

  const upstream = await fetch(target, {
    method: req.method,
    headers,
    body: ["GET", "HEAD"].includes(req.method || "GET") ? undefined : body,
  });

  res.writeHead(upstream.status, Object.fromEntries(upstream.headers.entries()));
  if (!upstream.body) {
    res.end();
    return;
  }

  const reader = upstream.body.getReader();
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    res.write(Buffer.from(value));
  }
  res.end();
}

server.listen(port, host, () => {
  console.log(`Tidal Echo local preview: http://${host === "0.0.0.0" ? "127.0.0.1" : host}:${port}/`);
});

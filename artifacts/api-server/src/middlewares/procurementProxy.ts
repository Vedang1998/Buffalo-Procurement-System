import http from "node:http";
import type { Request, Response, NextFunction } from "express";

const PROCUREMENT_PORT = 8000;
const PREFIX = "/procurement";

/**
 * Reverse-proxies /procurement/* to the Procurement OS (uvicorn on :8000),
 * stripping the prefix. Streams the raw request body, so it must be mounted
 * BEFORE any body-parsing middleware.
 */
export function procurementProxy(
  req: Request,
  res: Response,
  next: NextFunction,
): void {
  if (req.url !== PREFIX && !req.url.startsWith(`${PREFIX}/`)) {
    next();
    return;
  }
  const targetPath = req.url.slice(PREFIX.length) || "/";
  const upstream = http.request(
    {
      host: "127.0.0.1",
      port: PROCUREMENT_PORT,
      method: req.method,
      path: targetPath,
      headers: { ...req.headers, host: `127.0.0.1:${PROCUREMENT_PORT}` },
    },
    (upstreamRes) => {
      res.writeHead(upstreamRes.statusCode ?? 502, upstreamRes.headers);
      upstreamRes.pipe(res);
    },
  );
  upstream.on("error", () => {
    if (!res.headersSent) {
      res
        .status(502)
        .type("text/plain")
        .send("Procurement OS is not running (port 8000).");
    }
  });
  req.pipe(upstream);
}

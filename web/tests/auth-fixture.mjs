import { createServer } from "node:http";

// A separate network origin, with real CORS enforcement and OPTIONS requests.
// Reuse the registry fixture's descriptor-consistent index/config/layer bytes.
export async function authRegistry(upstream, { exposeChallenge = false, basic = false, credentialed = false } = {}) {
  const fixture = { requests: [], issued: 0, token: null, revokedStatus: 401, rejectFresh: false };
  const server = createServer(async (request, response) => {
    const url = new URL(request.url, fixture.origin);
    fixture.requests.push({
      method: request.method, path: url.pathname, query: Object.fromEntries(url.searchParams),
      authorization: request.headers.authorization, cookie: request.headers.cookie,
      preflightHeaders: request.headers["access-control-request-headers"],
    });
    response.setHeader("Access-Control-Allow-Origin", credentialed ? upstream : "*");
    if (credentialed) response.setHeader("Access-Control-Allow-Credentials", "true");
    response.setHeader("Cross-Origin-Resource-Policy", "cross-origin");
    response.setHeader("Cache-Control", "no-store");
    if (request.method === "OPTIONS") {
      response.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
      response.setHeader("Access-Control-Allow-Headers", "Authorization, Accept");
      response.writeHead(204).end();
      return;
    }
    if (url.pathname === "/20180419/docker/token") {
      if (url.searchParams.get("service") !== "fixture-registry"
        || !["repository:demo/image:pull", "repository:demo/many:pull", "registry:catalog:*"].includes(url.searchParams.get("scope"))) {
        response.writeHead(400).end("incorrect service or scope");
        return;
      }
      fixture.token = `fixture-token-${++fixture.issued}`;
      response.setHeader("Content-Type", "application/json");
      response.end(JSON.stringify({ access_token: fixture.token, expires_in: 3600 }));
      return;
    }
    if (url.pathname.startsWith("/v2/")) {
      if (!fixture.token || request.headers.authorization !== `Bearer ${fixture.token}` || fixture.rejectFresh) {
        response.setHeader("WWW-Authenticate", basic ? 'Basic realm="private"'
          : `Bearer realm="${fixture.origin}/20180419/docker/token",service="fixture-registry",scope="repository:demo/image:pull"`);
        // Match OCIR: the challenge exists on the wire but is invisible to JS.
        response.setHeader("Access-Control-Expose-Headers", exposeChallenge ? "WWW-Authenticate" : "opc-request-id");
        response.writeHead(request.headers.authorization ? fixture.revokedStatus : 401).end();
        return;
      }
      if (url.pathname.endsWith("/tags/list")) {
        response.setHeader("Content-Type", "application/json");
        response.end(JSON.stringify({ name: url.pathname.includes("/many/") ? "demo/many" : "demo/image", tags: ["multi"] }));
        return;
      }
      try {
        const result = await fetch(new URL(url.pathname, upstream));
        const body = Buffer.from(await result.arrayBuffer());
        const kind = url.pathname.includes("/manifests/sha256:") ? "manifest"
          : result.headers.get("content-type")?.includes("image.config") ? "config" : "layer";
        if (fixture.corrupt === kind) body[0] ^= 1;
        response.setHeader("Content-Type", result.headers.get("content-type"));
        response.writeHead(result.status).end(body);
      } catch (error) {
        response.writeHead(500).end(String(error));
      }
      return;
    }
    response.writeHead(404).end();
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  fixture.origin = `http://127.0.0.1:${server.address().port}`;
  fixture.close = () => new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
    server.closeAllConnections();
  });
  fixture.config = { [fixture.origin]: { realm: `${fixture.origin}/20180419/docker/token`, service: "fixture-registry" } };
  return fixture;
}

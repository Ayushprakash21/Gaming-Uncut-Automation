interface KVNamespace {
  get(key: string): Promise<string | null>;
}

interface Env {
  BACKEND_URL?: string;
  INTERNAL_API_SECRET?: string;
  NODE_KV?: KVNamespace;
}

export async function onRequest(context: { request: Request; env: Env }) {
  const url = new URL(context.request.url);

  // 1. Short-circuit preflight OPTIONS
  if (context.request.method === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Max-Age": "86400",
      },
    });
  }

  // 2. Resolve live backend target dynamically from KV (highest priority) or env var
  let dynamicBackend: string | null = null;
  let dynamicSecret: string | null = null;

  if (context.env?.NODE_KV) {
    try {
      dynamicBackend = await context.env.NODE_KV.get("backend_url");
      dynamicSecret = await context.env.NODE_KV.get("internal_api_secret");
    } catch {
      // Fallback to env var if KV read fails
    }
  }

  const backendTarget = (dynamicBackend || context.env?.BACKEND_URL || "https://gaming-uncut-backend.gaminguncut1.workers.dev").trim().replace(/\/+$/, "");
  const internalSecret = dynamicSecret || context.env?.INTERNAL_API_SECRET;

  const isHealthCheck = url.pathname === "/api/health" || url.pathname === "/api/system/health";

  // If node is explicitly offline or unconfigured
  if (!backendTarget || backendTarget === "offline") {
    if (isHealthCheck) {
      return new Response(
        JSON.stringify({
          status: "offline",
          node_online: false,
          app: "Gaming Uncut Automation",
          message: "Gaming Uncut GPU Processing Node is offline. Please launch 'python run.py' on your Windows node.",
          hardware: {
            gpu: "Offline",
            nvenc_enabled: false,
            target_resolution: "1080x1920"
          },
          worker: {
            is_alive: false,
            active_jobs: 0
          }
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          }
        }
      );
    }

    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: "NODE_OFFLINE",
          message: "Gaming Uncut GPU Processing Node is offline. Please launch 'python run.py' on your Windows PC to enable pipeline processing.",
          details: {
            path: url.pathname,
            target: backendTarget || "unconfigured"
          }
        },
        detail: "Gaming Uncut GPU Processing Node is offline. Please launch 'python run.py' on your Windows PC to enable pipeline processing."
      }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        }
      }
    );
  }

  // 3. Build target URL
  let targetUrl: URL;
  try {
    targetUrl = new URL(url.pathname + url.search, backendTarget);
  } catch (err: any) {
    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: "INVALID_BACKEND_URL",
          message: `Configured backend target '${backendTarget}' is invalid: ${err?.message || String(err)}`,
        },
        detail: `Invalid backend target: ${err?.message || String(err)}`
      }),
      {
        status: 502,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      }
    );
  }

  const headers = new Headers(context.request.headers);
  headers.set("Host", targetUrl.host);

  // Attach internal secret if configured
  if (internalSecret) {
    headers.set("X-Internal-Secret", internalSecret);
  }

  const init: RequestInit = {
    method: context.request.method,
    headers: headers,
    redirect: "follow",
  };

  if (context.request.method !== "GET" && context.request.method !== "HEAD") {
    init.body = context.request.body;
    // @ts-ignore
    init.duplex = "half";
  }

  // 4. Execute fetch with 15s timeout
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 15000);
  // @ts-ignore
  init.signal = controller.signal;

  try {
    const response = await fetch(targetUrl.toString(), init);
    clearTimeout(timeoutId);

    // Check for Cloudflare Edge origin errors (530, 520..526), VPC gateway errors (502..504), or upstream non-200 HTML pages
    const isUpstreamError = response.status === 502 || response.status === 503 || response.status === 504 || response.status === 530 || (response.status >= 520 && response.status <= 526);
    const contentType = (response.headers.get("content-type") || "").toLowerCase();
    const isHtmlError = response.status !== 200 && (contentType.includes("text/html") || contentType.includes("text/plain"));

    if (isUpstreamError || isHtmlError) {
      if (isHealthCheck) {
        return new Response(
          JSON.stringify({
            status: "offline",
            node_online: false,
            app: "Gaming Uncut Automation",
            message: "Gaming Uncut GPU Processing Node is unreachable. Please verify 'python run.py' is running on your Windows node.",
            hardware: {
              gpu: "Offline",
              nvenc_enabled: false,
              target_resolution: "1080x1920"
            },
            worker: {
              is_alive: false,
              active_jobs: 0
            }
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json",
              "Access-Control-Allow-Origin": "*",
            }
          }
        );
      }

      return new Response(
        JSON.stringify({
          success: false,
          error: {
            code: "NODE_OFFLINE",
            message: "Gaming Uncut GPU Processing Node is offline or unreachable. Please verify that 'python run.py' is running on your local Windows PC.",
            details: {
              target: backendTarget,
              path: url.pathname,
              upstream_status: response.status
            }
          },
          detail: "Gaming Uncut GPU Processing Node is offline or unreachable. Please verify that 'python run.py' is running on your local Windows PC."
        }),
        {
          status: 503,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          }
        }
      );
    }

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Access-Control-Allow-Origin", "*");
    responseHeaders.set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, PATCH, OPTIONS");
    responseHeaders.set("Access-Control-Allow-Headers", "*");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (err: any) {
    clearTimeout(timeoutId);
    const isTimeout = err?.name === "AbortError";

    if (isHealthCheck) {
      return new Response(
        JSON.stringify({
          status: "offline",
          node_online: false,
          app: "Gaming Uncut Automation",
          message: isTimeout
            ? "Health check timed out contacting GPU processing node."
            : "Gaming Uncut GPU Processing Node is offline. Please launch 'python run.py' on your Windows PC.",
          hardware: {
            gpu: "Offline",
            nvenc_enabled: false,
            target_resolution: "1080x1920"
          },
          worker: {
            is_alive: false,
            active_jobs: 0
          }
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          }
        }
      );
    }

    const status = isTimeout ? 504 : 503;
    const code = isTimeout ? "GATEWAY_TIMEOUT" : "NODE_OFFLINE";
    const message = isTimeout
      ? "Request to Gaming Uncut backend processing node timed out after 15 seconds."
      : "Gaming Uncut GPU processing node is currently offline or unreachable. Please verify that 'python run.py' is running on your Windows PC.";

    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: code,
          message: message,
          details: {
            target: backendTarget,
            path: url.pathname,
            error: err?.message || String(err),
          },
        },
        detail: message,
      }),
      {
        status: status,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}

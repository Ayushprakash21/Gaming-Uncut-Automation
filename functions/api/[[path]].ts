interface Env {
  BACKEND_URL?: string;
  INTERNAL_API_SECRET?: string;
}

export async function onRequest(context: { request: Request; env: Env }) {
  const url = new URL(context.request.url);

  // 1. Read stable backend target from Pages environment variable or fallback
  const backendTarget = (context.env?.BACKEND_URL || "https://backend.gaming-uncut.pages.dev").trim().replace(/\/+$/, "");

  // 2. Short-circuit preflight OPTIONS
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

  // 3. Build target URL
  const targetUrl = new URL(url.pathname + url.search, backendTarget);

  const headers = new Headers(context.request.headers);
  headers.set("Host", new URL(backendTarget).host);

  // Attach internal secret if configured
  if (context.env?.INTERNAL_API_SECRET) {
    headers.set("X-Internal-Secret", context.env.INTERNAL_API_SECRET);
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
    const status = isTimeout ? 504 : 502;
    const code = isTimeout ? "GATEWAY_TIMEOUT" : "BACKEND_UNAVAILABLE";
    const message = isTimeout
      ? "Request to Gaming Uncut backend processing node timed out after 15 seconds."
      : "Gaming Uncut processing node is currently offline or unreachable. Please verify that the local NVENC GPU node and tunnel service are running.";

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

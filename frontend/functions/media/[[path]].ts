interface KVNamespace {
  get(key: string): Promise<string | null>;
}

interface Env {
  BACKEND_URL?: string;
  NODE_KV?: KVNamespace;
}

export async function onRequest(context: { request: Request; env: Env }) {
  const url = new URL(context.request.url);

  let dynamicBackend: string | null = null;
  if (context.env?.NODE_KV) {
    try {
      dynamicBackend = await context.env.NODE_KV.get("backend_url");
    } catch {
      // Fallback
    }
  }

  const backendTarget = (dynamicBackend || context.env?.BACKEND_URL || "https://gaming-uncut-backend.gaminguncut1.workers.dev").trim().replace(/\/+$/, "");

  if (!backendTarget || backendTarget === "offline") {
    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: "NODE_OFFLINE",
          message: "Media source node is currently offline. Please start 'python run.py' on your Windows node.",
        },
      }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }

  let targetUrl: URL;
  try {
    targetUrl = new URL(url.pathname + url.search, backendTarget);
  } catch (err: any) {
    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: "INVALID_BACKEND_URL",
          message: `Invalid backend target: ${err?.message || String(err)}`,
        },
      }),
      {
        status: 502,
        headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" }
      }
    );
  }

  const headers = new Headers(context.request.headers);
  headers.set("Host", targetUrl.host);

  const init: RequestInit = {
    method: context.request.method,
    headers: headers,
    redirect: "follow",
  };

  try {
    const response = await fetch(targetUrl.toString(), init);
    const isCloudflareOriginError = response.status === 530 || (response.status >= 520 && response.status <= 526);

    if (isCloudflareOriginError) {
      return new Response(
        JSON.stringify({
          success: false,
          error: {
            code: "NODE_OFFLINE",
            message: "Media host is currently offline or unreachable.",
          },
        }),
        {
          status: 503,
          headers: {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
          },
        }
      );
    }

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Access-Control-Allow-Origin", "*");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (err: any) {
    return new Response(
      JSON.stringify({
        success: false,
        error: {
          code: "MEDIA_BACKEND_UNAVAILABLE",
          message: "Media source on processing node is currently unreachable.",
          details: { error: err?.message || String(err) },
        },
      }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}

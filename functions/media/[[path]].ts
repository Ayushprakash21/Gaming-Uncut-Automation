interface Env {
  BACKEND_URL?: string;
}

export async function onRequest(context: { request: Request; env: Env }) {
  const url = new URL(context.request.url);
  const backendTarget = (context.env?.BACKEND_URL || "https://backend.gaming-uncut.pages.dev").trim().replace(/\/+$/, "");
  const targetUrl = new URL(url.pathname + url.search, backendTarget);

  const headers = new Headers(context.request.headers);
  headers.set("Host", new URL(backendTarget).host);

  const init: RequestInit = {
    method: context.request.method,
    headers: headers,
    redirect: "follow",
  };

  try {
    const response = await fetch(targetUrl.toString(), init);
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
        status: 502,
        headers: {
          "Content-Type": "application/json",
          "Access-Control-Allow-Origin": "*",
        },
      }
    );
  }
}

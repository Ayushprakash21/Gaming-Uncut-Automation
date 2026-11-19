interface Env {
  BACKEND_VPC: Fetcher;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const targetUrl = new URL(url.pathname + url.search, "http://127.0.0.1:8765");

    const reqHeaders = new Headers(request.headers);
    reqHeaders.set("Host", "127.0.0.1:8765");

    try {
      const response = await env.BACKEND_VPC.fetch(targetUrl.toString(), {
        method: request.method,
        headers: reqHeaders,
        body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
        redirect: "manual"
      });

      const resHeaders = new Headers(response.headers);
      resHeaders.set("Access-Control-Allow-Origin", "*");
      resHeaders.set("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
      resHeaders.set("Access-Control-Allow-Headers", "*");

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: resHeaders
      });
    } catch (err: any) {
      return new Response(JSON.stringify({
        error: "VPC Gateway Error",
        message: err?.message || String(err),
        timestamp: new Date().toISOString()
      }), {
        status: 502,
        headers: { "Content-Type": "application/json" }
      });
    }
  }
};

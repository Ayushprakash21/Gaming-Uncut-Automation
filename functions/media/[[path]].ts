export async function onRequest(context: { request: Request }) {
  const url = new URL(context.request.url);
  const backendTarget = "https://oliver-powered-obtain-wedding.trycloudflare.com";
  const targetUrl = new URL(url.pathname + url.search, backendTarget);

  const headers = new Headers(context.request.headers);
  headers.set("Host", new URL(backendTarget).host);

  const init: RequestInit = {
    method: context.request.method,
    headers: headers,
    redirect: "follow",
  };

  const response = await fetch(targetUrl.toString(), init);
  const responseHeaders = new Headers(response.headers);
  responseHeaders.set("Access-Control-Allow-Origin", "*");

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

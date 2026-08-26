/**
 * Refuse every direct request under /internal/.
 *
 * The per-ticker score files live here. Free lookups are metered, and a file
 * the browser can fetch for itself cannot be metered -- anyone could skip the
 * tool, hit /internal/scores/AAPL.json directly, and walk the whole universe
 * a request at a time. So nothing under this prefix is ever served to a
 * client.
 *
 * The tool Function still reads these files, but through the ASSETS binding,
 * which serves the underlying static asset directly and never passes through
 * Functions routing -- so it does not hit this middleware.
 *
 * 404 rather than 403: a 403 would confirm the path exists and invite probing.
 * There is nothing here as far as the outside world is concerned.
 */
export async function onRequest() {
  return new Response("Not found", {
    status: 404,
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "no-store",
      "x-content-type-options": "nosniff",
    },
  });
}

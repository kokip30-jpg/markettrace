import { createClient } from "npm:@supabase/supabase-js@2";

const bucket = "markettrace-private";
const file = "options/summary.json";
const allowedOrigins = new Set([
  "https://optionscope.koki-p30.chatgpt.site",
  "https://kokip30-jpg.github.io",
]);

function cors(req: Request) {
  const origin = req.headers.get("origin") || "";
  return {
    "Access-Control-Allow-Origin": allowedOrigins.has(origin) ? origin : "https://optionscope.koki-p30.chatgpt.site",
    "Access-Control-Allow-Headers": "content-type",
    "Vary": "Origin",
  };
}

Deno.serve(async (req) => {
  const headers = cors(req);
  if (req.method === "OPTIONS") return new Response("ok", { headers });
  if (req.method !== "GET") return new Response("Method not allowed", { status: 405, headers });

  // This function intentionally serves only a prepared, non-account-specific
  // summary. The Alpaca credentials and the full option chain never leave the
  // MarketTrace ingestion workflow or private storage.
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
  const { data, error } = await admin.storage.from(bucket).download(file);
  if (error || !data) {
    return Response.json({ updated: null, ideas: [], status: "waiting-for-first-refresh" }, {
      status: 503,
      headers: { ...headers, "Cache-Control": "no-store" },
    });
  }
  return new Response(data.stream(), {
    headers: {
      ...headers,
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "public, max-age=120, stale-while-revalidate=180",
    },
  });
});

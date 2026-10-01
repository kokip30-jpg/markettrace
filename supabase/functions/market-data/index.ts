import { createClient } from "npm:@supabase/supabase-js@2";

const bucket = "markettrace-private";
const allowed = /^(?:[a-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.json$/;
const cors = {
  "Access-Control-Allow-Origin": "https://kokip30-jpg.github.io",
  "Access-Control-Allow-Headers": "authorization, apikey, content-type",
  "Vary": "Origin",
};

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: cors });
  if (req.method !== "GET") return new Response("Method not allowed", { status: 405, headers: cors });
  const path = new URL(req.url).searchParams.get("path") || "";
  if (!allowed.test(path) || path.includes("..")) return new Response("Not found", { status: 404, headers: cors });

  // verify_jwt blocks anonymous requests before this handler. The service key
  // is used only inside the function to read the private Storage bucket.
  const admin = createClient(
    Deno.env.get("SUPABASE_URL")!,
    Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!,
  );
  const { data, error } = await admin.storage.from(bucket).download(path);
  if (error || !data) return new Response("Not found", { status: 404, headers: cors });
  return new Response(data.stream(), {
    headers: { ...cors, "Content-Type": "application/json; charset=utf-8", "Cache-Control": "private, max-age=120" },
  });
});

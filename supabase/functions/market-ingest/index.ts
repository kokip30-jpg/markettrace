import { createClient } from "npm:@supabase/supabase-js@2";
import { createRemoteJWKSet, jwtVerify } from "npm:jose@6";

const bucket = "markettrace-private";
const repo = "kokip30-jpg/markettrace";
const allowed = /^(?:[a-z0-9_-]+\/)*[A-Za-z0-9_.-]+\.json$/;
const githubKeys = createRemoteJWKSet(new URL("https://token.actions.githubusercontent.com/.well-known/jwks"));

async function trustedWorkflow(req: Request) {
  const token = req.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!token) throw new Error("Missing workflow identity");
  const { payload } = await jwtVerify(token, githubKeys, {
    issuer: "https://token.actions.githubusercontent.com",
    audience: "markettrace-ingest",
  });
  if (payload.repository !== repo || payload.ref !== "refs/heads/main") throw new Error("Untrusted workflow");
}

Deno.serve(async (req) => {
  try {
    if (req.method !== "POST") return new Response("Method not allowed", { status: 405 });
    await trustedWorkflow(req);
    const action = new URL(req.url).searchParams.get("action");
    const admin = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
    const made = await admin.storage.createBucket(bucket, { public: false, fileSizeLimit: "6MB" });
    if (made.error && !/already exists/i.test(made.error.message)) throw made.error;

    if (action === "sign") {
      const { paths } = await req.json();
      if (!Array.isArray(paths) || paths.length > 100 || paths.some((p) => typeof p !== "string" || !allowed.test(p) || p.includes(".."))) {
        return Response.json({ error: "Invalid paths" }, { status: 400 });
      }
      const uploads: Record<string, string> = {};
      for (const path of paths) {
        const { data, error } = await admin.storage.from(bucket).createSignedUploadUrl(path, { upsert: true });
        if (error || !data) throw error || new Error("Could not sign upload");
        // Storage currently returns an absolute upload URL. Keep the SDK output
        // intact; older SDK responses are normalized by the Action client.
        uploads[path] = data.signedUrl;
      }
      return Response.json({ uploads });
    }
    if (action === "list") {
      const files: string[] = [];
      async function walk(prefix = "") {
        const { data, error } = await admin.storage.from(bucket).list(prefix, { limit: 1000, sortBy: { column: "name", order: "asc" } });
        if (error) throw error;
        for (const item of data || []) {
          const path = `${prefix}${item.name}`;
          if (item.id) files.push(path); else await walk(`${path}/`);
        }
      }
      await walk();
      return Response.json({ files });
    }
    if (action === "download") {
      const { paths } = await req.json();
      if (!Array.isArray(paths) || paths.length > 100 || paths.some((p) => typeof p !== "string" || !allowed.test(p))) return Response.json({ error: "Invalid paths" }, { status: 400 });
      const downloads: Record<string, string> = {};
      for (const path of paths) {
        const { data, error } = await admin.storage.from(bucket).createSignedUrl(path, 900);
        if (error || !data) throw error || new Error("Could not sign download");
        downloads[path] = data.signedUrl;
      }
      return Response.json({ downloads });
    }
    return Response.json({ error: "Unknown action" }, { status: 400 });
  } catch (error) {
    return Response.json({ error: "Unauthorized ingestion request" }, { status: 401 });
  }
});

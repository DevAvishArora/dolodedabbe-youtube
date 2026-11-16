// Cloudflare Worker: receives Telegram webhook calls and starts the GitHub
// Actions workflow. Always answers 200 so Telegram does not retry in a loop.
//
// Worker secrets: TG_WEBHOOK_SECRET, TG_CHAT_ID, TG_OWNER_ID, GH_REPO, GH_TOKEN

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (secret !== env.TG_WEBHOOK_SECRET) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad json");
    }
    const msg = update.message || update.channel_post;
    if (!msg) return new Response("ignored");

    const inGroup = String(msg.chat.id) === env.TG_CHAT_ID;
    const fromOwner =
      msg.chat.type === "private" && String(msg.from?.id) === env.TG_OWNER_ID;
    const isVideo =
      Boolean(msg.video) ||
      (msg.document?.mime_type || "").startsWith("video/");
    const isReply = Boolean(msg.reply_to_message && msg.text);

    // Only wake the pipeline for clips, replies in the group, or the owner.
    if (!(fromOwner || (inGroup && (isVideo || isReply)))) {
      return new Response("ignored");
    }

    const res = await fetch(
      `https://api.github.com/repos/${env.GH_REPO}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GH_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "dolladabbe-telegram-worker",
        },
        body: JSON.stringify({
          event_type: "telegram_update",
          client_payload: { update },
        }),
      }
    );
    return new Response(res.ok ? "ok" : `dispatch failed: ${res.status}`);
  },
};

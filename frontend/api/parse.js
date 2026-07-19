// Vercel Edge Function — parses a field service note using Grok AI
export default async function handler(req) {
  if (req.method !== 'POST') {
    return new Response(JSON.stringify({ error: 'POST only' }), { status: 405, headers: { 'Content-Type': 'application/json' }});
  }

  const body = await req.json();
  const note = body.note || '';

  if (!note.trim()) {
    return new Response(JSON.stringify({ error: 'No note provided' }), { status: 400, headers: { 'Content-Type': 'application/json' }});
  }

  const prompt = `You are a field service note parser. Given a worker's voice or text note, extract structured data.

Return JSON with these fields:
{
  "account": "the client/account name mentioned",
  "status": "all_good | issues_found | needs_supplies | follow_up_needed | urgent",
  "issues": ["problems found"],
  "supplies": ["supplies needed for next visit"],
  "follow_ups": ["things to do next time"],
  "customer_requests": ["things the client asked for"],
  "summary": "one-line clean summary of the stop"
}

Rules:
- If the worker says "all good", status is "all_good"
- Use the exact words the worker used — don't embellish
- Keep it brief — these are between-stop notes

Worker note: ${note}

JSON:`;

  const apiKey = (process.env.XAI_API_KEY || '').trim();

  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'API not configured' }), { status: 500, headers: { 'Content-Type': 'application/json' }});
  }

  const t0 = Date.now();

  try {
    const resp = await fetch('https://api.x.ai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${apiKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        model: 'grok-4-mini',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.1,
        max_tokens: 500,
        response_format: { type: 'json_object' }
      })
    });

    const data = await resp.json();
    const parsed = JSON.parse(data.choices[0].message.content);
    parsed.processing_ms = Date.now() - t0;

    return new Response(JSON.stringify({ ok: true, parsed, note }), {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message || 'Parse failed' }), { status: 500, headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }});
  }
}

export const config = { runtime: 'edge' };

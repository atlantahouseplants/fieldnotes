// Vercel Edge Function — parses field service notes via xAI Grok
export default async function handler(req) {
  if (req.method !== 'POST') {
    return new Response(JSON.stringify({ error: 'POST only' }), { 
      status: 405, headers: { 'Content-Type': 'application/json' }
    });
  }

  let body;
  try { body = await req.json(); } catch { 
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), { 
      status: 400, headers: { 'Content-Type': 'application/json' }
    });
  }
  
  const note = (body.note || '').trim();
  if (!note) {
    return new Response(JSON.stringify({ error: 'No note provided' }), { 
      status: 400, headers: { 'Content-Type': 'application/json' }
    });
  }

  const key = (process.env.XAI_API_KEY || '').trim();
  if (!key) {
    return new Response(JSON.stringify({ error: 'API key not configured' }), { 
      status: 500, headers: { 'Content-Type': 'application/json' }
    });
  }

  const t0 = Date.now();

  const prompt = `You are a field service note parser. Given a worker's voice/text note, extract structured data.
Return valid JSON:
{"account":"the client/account name mentioned","status":"all_good|issues_found|needs_supplies|follow_up_needed|urgent","issues":[],"supplies":[],"follow_ups":[],"customer_requests":[],"summary":"one-line clean summary"}
Rules: if "all good", status is all_good. Use exact words. Keep it brief.
Worker note: ${note}
JSON:`;

  try {
    const resp = await fetch('https://api.x.ai/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${key}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        model: 'grok-4.5',
        messages: [{ role: 'user', content: prompt }],
        temperature: 0.1,
        max_tokens: 500,
        response_format: { type: 'json_object' }
      })
    });

    const data = await resp.json();
    
    if (!resp.ok || data.error) {
      return new Response(JSON.stringify({ 
        error: data.error?.message || `API error: ${resp.status}`,
        detail: JSON.stringify(data).substring(0, 200)
      }), {
        status: 500, headers: { 'Content-Type': 'application/json' }
      });
    }

    const parsed = JSON.parse(data.choices[0].message.content);
    parsed.processing_ms = Date.now() - t0;

    return new Response(JSON.stringify({ ok: true, parsed, note }), {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' }
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message || 'Parse failed' }), {
      status: 500, headers: { 'Content-Type': 'application/json' }
    });
  }
}

export const config = { runtime: 'edge' };

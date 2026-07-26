#!/usr/bin/env python3
"""FieldNotes branded HyperFrames video generator.

Takes a JSON spec describing scenes, emits a branded multi-scene HyperFrames
composition (charcoal #1A1D21 / lime #C6F135 / Inter), renders it to MP4 via
`npx hyperframes`, and verifies the output (ffprobe + frame stddev).

Usage:
    fieldnotes_hf_video.py <spec.json>

Spec:
{
  "id": "2026-07-28-proof-of-service",           # required — used for workdir naming
  "out": "/abs/path/video-previews/<id>.mp4",    # required — final MP4 path
  "scenes": [                                     # required, 2..8 scenes
    {"type": "hook",  "kicker": "Tuesday, 7:52 AM",
     "headline": "Your tech is at the gate.",
     "sub": "The gate code is in a text thread from March.",
     "say": "Your tech is at the gate, and the gate code is buried in a text thread from March."},
    {"type": "value", "kicker": "Step 1",
     "headline": "He sends one text after each stop.",
     "say": "After each stop he sends one text, like he's texting a buddy."},
    {"type": "brand", "tagline": "Every job detail. One voice memo.\nLogged before the truck leaves."},
    {"type": "cta"}                                # standardized end-card, no fields needed
  ],
  "voice": "en-US-GuyNeural"                      # optional — edge-tts voice (default Guy)
}

NARRATION ("say" per scene): any scene may include a "say" string — spoken
voiceover for that scene, generated FREE via edge-tts (Microsoft, no key, no
cost; swap-in for ElevenLabs later). Keep each "say" <= ~25 words so it fits
the scene duration; the generator speeds audio up to ~1.4x to fit and pads
short audio with silence. Scenes without "say" get silence. If NO scene has
"say", a silent AAC track is muxed instead (platforms expect an audio stream).

Scene types (default durations, overridable with "duration": seconds):
  hook  (4.0s) — kicker + big headline + dim sub. The scroll-stopper.
  value (3.5s) — kicker + headline + optional sub. One beat per scene.
  brand (2.5s) — lime rule + "FieldNotes" + tagline. Usually second-to-last.
  cta   (3.0s) — STANDARD END-CARD (the reusable sub-composition piece):
                 fieldnotesapp.io/app/try.html + "Free to try. Built for the field."
                 Always identical across videos. Don't customize.

Exit codes: 0 = rendered + verified; non-zero = failure (cron should fall back
to the PIL renderer fieldnotes_render_video.py).
"""
import json
import html
import os
import shutil
import subprocess
import sys
import tempfile

CONTENT_ENGINE = "/home/wallg/fieldnotes/marketing/content-engine"
WORKROOT = os.path.join(CONTENT_ENGINE, "hf-compositions")

DEFAULT_DUR = {"hook": 4.0, "value": 3.5, "brand": 2.5, "cta": 3.0}

PACKAGE_JSON = {
    "name": "fieldnotes-hf",
    "private": True,
    "type": "module",
}

HYPERFRAMES_JSON = {
    "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
    "registry": "https://raw.githubusercontent.com/heygen-com/hyperframes/main/registry",
    "paths": {"blocks": "compositions", "components": "compositions/components", "assets": "assets"},
    "media": {"autoProxy": True},
}


def esc(s):
    return html.escape(s or "", quote=False).replace("\n", "<br />")


def scene_html(i, scene, start, dur):
    t = scene["type"]
    sid = f"s{i}"
    if t == "cta":
        # Standard branded end-card — the reusable sub-composition piece.
        inner = f'''      <div id="{sid}" class="scene clip" data-start="{start}" data-duration="{dur}" data-track-index="{i}">
        <div class="cta-box" id="{sid}-box">
          <div class="cta-url" id="{sid}-url">fieldnotesapp.io/app/try.html</div>
          <div class="cta-sub" id="{sid}-sub">Free to try. Built for the field.</div>
        </div>
      </div>'''
    elif t == "brand":
        tagline = esc(scene.get("tagline", "Every job detail. One voice memo.\nLogged before the truck leaves."))
        inner = f'''      <div id="{sid}" class="scene clip" data-start="{start}" data-duration="{dur}" data-track-index="{i}">
        <div class="rule" id="{sid}-rule"></div>
        <div class="brand" id="{sid}-brand">FieldNotes</div>
        <div class="tagline" id="{sid}-tag">{tagline}</div>
      </div>'''
    else:  # hook / value
        kicker = esc(scene.get("kicker", ""))
        headline = esc(scene.get("headline", ""))
        sub = esc(scene.get("sub", ""))
        sub_html = f'\n        <div class="subline" id="{sid}-sub">{sub}</div>' if sub else ""
        cls = "hook" if t == "hook" else "value-line"
        inner = f'''      <div id="{sid}" class="scene clip" data-start="{start}" data-duration="{dur}" data-track-index="{i}">
        <div class="kicker" id="{sid}-kicker">{kicker}</div>
        <div class="{cls}" id="{sid}-head">{headline}</div>{sub_html}
      </div>'''
    return inner


def scene_tweens(i, scene, start):
    sid = f"s{i}"
    t = scene["type"]
    a = start + 0.10
    b = start + 0.35
    c = start + 0.65
    lines = []
    if t == "cta":
        lines.append(f'      tl.from("#{sid}-box", {{ scale: 0.92, opacity: 0, duration: 0.6 }}, {a:.2f});')
        lines.append(f'      tl.from("#{sid}-sub", {{ y: 20, opacity: 0, duration: 0.5 }}, {b:.2f});')
    elif t == "brand":
        lines.append(f'      tl.from("#{sid}-rule", {{ scaleX: 0, transformOrigin: "left", duration: 0.5 }}, {a:.2f});')
        lines.append(f'      tl.from("#{sid}-brand", {{ y: 50, opacity: 0, duration: 0.6 }}, {b:.2f});')
        lines.append(f'      tl.from("#{sid}-tag", {{ y: 30, opacity: 0, duration: 0.6 }}, {c:.2f});')
    else:
        lines.append(f'      tl.from("#{sid}-kicker", {{ y: -30, opacity: 0, duration: 0.5 }}, {a:.2f});')
        lines.append(f'      tl.from("#{sid}-head", {{ y: 40, opacity: 0, duration: 0.6 }}, {b:.2f});')
        if scene.get("sub"):
            lines.append(f'      tl.from("#{sid}-sub", {{ y: 25, opacity: 0, duration: 0.5 }}, {c:.2f});')
    return "\n".join(lines)


def build_html(scenes):
    total = 0.0
    parts, tweens, timings = [], [], []
    for i, sc in enumerate(scenes):
        if sc["type"] not in DEFAULT_DUR:
            raise SystemExit(f"unknown scene type: {sc['type']}")
        dur = float(sc.get("duration", DEFAULT_DUR[sc["type"]]))
        parts.append(scene_html(i, sc, round(total, 2), round(dur, 2)))
        tweens.append(scene_tweens(i, sc, total))
        timings.append((total, dur, sc))
        total += dur
    scenes_html = "\n\n".join(parts)
    tweens_js = "\n".join(tweens)
    return f'''<!doctype html>
<html lang="en" data-resolution="portrait">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=1080, height=1920" />
    <script src="https://cdn.jsdelivr.net/npm/gsap@3.14.2/dist/gsap.min.js"></script>
    <style>
      * {{ margin: 0; padding: 0; box-sizing: border-box; }}
      html, body {{
        margin: 0;
        width: 1080px;
        height: 1920px;
        overflow: hidden;
        background: #1A1D21;
      }}
      body {{ font-family: "Inter", sans-serif; }}
      .scene {{
        position: absolute;
        inset: 0;
        display: flex;
        flex-direction: column;
        justify-content: center;
        padding: 120px 90px;
      }}
      .kicker {{
        color: #C6F135;
        font-size: 34px;
        font-weight: bold;
        letter-spacing: 6px;
        text-transform: uppercase;
        margin-bottom: 48px;
      }}
      .hook {{
        color: #FFFFFF;
        font-size: 76px;
        font-weight: bold;
        line-height: 1.25;
      }}
      .value-line {{
        color: #FFFFFF;
        font-size: 64px;
        font-weight: bold;
        line-height: 1.3;
      }}
      .subline {{
        color: #8A8F98;
        font-size: 44px;
        line-height: 1.4;
        margin-top: 40px;
      }}
      .brand {{
        color: #C6F135;
        font-size: 120px;
        font-weight: bold;
        letter-spacing: -2px;
      }}
      .tagline {{
        color: #FFFFFF;
        font-size: 48px;
        line-height: 1.4;
        margin-top: 36px;
      }}
      .rule {{
        width: 180px;
        height: 8px;
        background: #C6F135;
        margin-bottom: 56px;
      }}
      .cta-box {{
        border: 4px solid #C6F135;
        border-radius: 24px;
        padding: 56px 48px;
        text-align: center;
      }}
      .cta-url {{
        color: #C6F135;
        font-size: 52px;
        font-weight: bold;
      }}
      .cta-sub {{
        color: #8A8F98;
        font-size: 34px;
        margin-top: 24px;
      }}
    </style>
  </head>
  <body>
    <div
      id="root"
      data-composition-id="main"
      data-start="0"
      data-duration="{total:.2f}"
      data-width="1080"
      data-height="1920"
    >
{scenes_html}
    </div>

    <script>
      window.__timelines = window.__timelines || {{}};
      const tl = gsap.timeline({{ paused: true }});

      // Scene content tweens only — .clip handles scene visibility windows
{tweens_js}

      window.__timelines["main"] = tl;
    </script>
  </body>
</html>
''', total, timings


def tts_segment(text, voice, path):
    import asyncio
    import edge_tts

    async def _go():
        await edge_tts.Communicate(text, voice).save(path)

    asyncio.run(_go())


def build_narration(timings, voice, workdir):
    """TTS per scene ("say"), each fit to its scene duration, concatenated in
    scene order. Returns the narration wav path, or None on failure."""
    segs = []
    for i, (start, dur, sc) in enumerate(timings):
        say = (sc.get("say") or "").strip()
        seg = os.path.join(workdir, f"seg-{i}.wav")
        if say:
            mp3 = os.path.join(workdir, f"seg-{i}.mp3")
            if not os.path.exists(mp3):  # pre-pass may already have synthesized it
                try:
                    tts_segment(say, voice, mp3)
                except Exception as e:
                    print(f"[hf] TTS failed for scene {i}: {e}")
                    return None
            rc, out_d = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "csv=p=0", mp3], cwd=workdir)
            try:
                ad = float(out_d.strip())
            except ValueError:
                return None
            af = ""
            if ad > dur - 0.15:
                tempo = min(ad / max(dur - 0.15, 0.5), 1.45)
                af = f"atempo={tempo:.3f},"
                print(f"[hf] scene {i} narration {ad:.1f}s vs {dur:.1f}s scene — atempo {tempo:.2f}x")
            rc, o = run(["ffmpeg", "-y", "-v", "error", "-i", mp3,
                         "-af", af + "apad", "-t", f"{dur:.2f}",
                         "-ar", "44100", "-ac", "2", seg], cwd=workdir)
        else:
            rc, o = run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                         "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                         "-t", f"{dur:.2f}", seg], cwd=workdir)
        if rc != 0 or not os.path.exists(seg):
            print(f"[hf] segment build failed scene {i}: {o[-300:]}")
            return None
        segs.append(seg)
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    track = os.path.join(workdir, "narration.wav")
    rc, o = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", lst, "-c", "copy", track], cwd=workdir)
    if rc != 0 or not os.path.exists(track):
        print(f"[hf] concat failed: {o[-300:]}")
        return None
    return track


def run(cmd, cwd, timeout=600):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def verify(out, expected_dur):
    rc, info = run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height:format=duration",
        "-of", "json", out,
    ], cwd="/tmp")
    if rc != 0:
        return False, f"ffprobe failed: {info}"
    try:
        data = json.loads(info)
        st = data["streams"][0]
        dur = float(data["format"]["duration"])
        assert st["codec_name"] == "h264", st["codec_name"]
        assert (st["width"], st["height"]) == (1080, 1920), (st["width"], st["height"])
        assert abs(dur - expected_dur) < 1.0, f"duration {dur} vs expected {expected_dur}"
    except Exception as e:
        return False, f"ffprobe parse/assert: {e}\n{info}"
    # Frame stddev checks at 40% and 90% (90% proves the CTA end-card rendered)
    sys.path.insert(0, "/home/wallg/.hermes/hermes-agent/venv/lib/python3.11/site-packages")
    from PIL import Image, ImageStat
    for frac in (0.4, 0.9):
        t = expected_dur * frac
        frame = tempfile.mktemp(suffix=".png")
        rc, o = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", out,
                     "-frames:v", "1", frame], cwd="/tmp")
        if rc != 0 or not os.path.exists(frame):
            return False, f"frame extract at {t:.1f}s failed: {o}"
        std = ImageStat.Stat(Image.open(frame).convert("L")).stddev[0]
        os.unlink(frame)
        if std < 10:
            return False, f"frame at {t:.1f}s looks blank (stddev {std:.1f})"
    return True, f"h264 1080x1920, {dur:.2f}s (expected {expected_dur:.2f}s), frames non-blank"


def main():
    spec = json.load(open(sys.argv[1]))
    sid = spec["id"]
    out = spec["out"]
    scenes = spec["scenes"]
    if not (2 <= len(scenes) <= 8):
        raise SystemExit("need 2..8 scenes")

    workdir = os.path.join(WORKROOT, sid)
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    os.makedirs(workdir)

    # Narration pre-pass: synthesize "say" lines FIRST and stretch scene
    # durations to fit natural speech (audio + 0.35s breathing room), so the
    # voiceover sets the pace instead of getting tempo-crushed into the scene.
    voice = spec.get("voice", "en-US-GuyNeural")
    for i, sc in enumerate(scenes):
        say = (sc.get("say") or "").strip()
        if not say:
            continue
        mp3 = os.path.join(workdir, f"seg-{i}.mp3")
        try:
            tts_segment(say, voice, mp3)
            rc, out_d = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "csv=p=0", mp3], cwd=workdir)
            ad = float(out_d.strip())
            want = ad + 0.35
            default = float(sc.get("duration", DEFAULT_DUR[sc["type"]]))
            if want > default:
                sc["duration"] = round(want, 2)
                print(f"[hf] scene {i} stretched {default:.1f}s -> {sc['duration']:.1f}s to fit narration ({ad:.1f}s)")
        except Exception as e:
            print(f"[hf] WARN: TTS pre-pass failed scene {i} ({e}) — using default duration")

    html_text, total, timings = build_html(scenes)
    with open(os.path.join(workdir, "index.html"), "w") as f:
        f.write(html_text)
    with open(os.path.join(workdir, "package.json"), "w") as f:
        json.dump(PACKAGE_JSON, f, indent=2)
    with open(os.path.join(workdir, "hyperframes.json"), "w") as f:
        json.dump(HYPERFRAMES_JSON, f, indent=2)
    print(f"[hf] composition written: {workdir}/index.html ({len(scenes)} scenes, {total:.2f}s)")

    rc, o = run(["npx", "-y", "hyperframes", "check"], cwd=workdir, timeout=300)
    print(o[-2000:])
    if rc != 0:
        print("[hf] CHECK FAILED — composition has lint/runtime errors", file=sys.stderr)
        sys.exit(2)

    os.makedirs(os.path.dirname(out), exist_ok=True)
    rc, o = run(["npx", "-y", "hyperframes", "render", "--output", out], cwd=workdir, timeout=600)
    print(o[-2000:])
    if rc != 0 or not os.path.exists(out) or os.path.getsize(out) == 0:
        print("[hf] RENDER FAILED", file=sys.stderr)
        sys.exit(3)

    # Audio: narration track if any scene has "say" (edge-tts, $0); otherwise a
    # silent AAC track — platforms (IG Reels/TikTok) expect an audio stream
    # regardless (Jul 23 pitfall). HyperFrames render output has NO audio stream.
    voice = spec.get("voice", "en-US-GuyNeural")
    track = None
    if any((sc.get("say") or "").strip() for sc in scenes):
        print(f"[hf] building narration track (voice={voice})")
        track = build_narration(timings, voice, workdir)
        if not track:
            print("[hf] WARN: narration build failed — falling back to silent audio")
    if track:
        tmp = out + ".audio.mp4"
        rc, o = run(["ffmpeg", "-y", "-v", "error", "-i", out, "-i", track,
                     "-c:v", "copy", "-c:a", "aac", "-shortest", tmp], cwd="/tmp")
        if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, out)
            print("[hf] narration track muxed in")
        else:
            if os.path.exists(tmp):
                os.unlink(tmp)
            print("[hf] WARN: narration mux failed; publishing silent")
    else:
        rc, probe = run(["ffprobe", "-v", "error", "-select_streams", "a",
                         "-show_entries", "stream=codec_name", "-of", "csv=p=0", out], cwd="/tmp")
        if not probe.strip():
            tmp = out + ".audio.mp4"
            rc, o = run(["ffmpeg", "-y", "-v", "error", "-i", out,
                         "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                         "-c:v", "copy", "-c:a", "aac", "-shortest", tmp], cwd="/tmp")
            if rc == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, out)
                print("[hf] silent AAC track muxed in")
            else:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                print("[hf] WARN: could not mux silent audio; publishing without it")

    ok, msg = verify(out, total)
    print(f"[hf] verify: {msg}")
    if not ok:
        sys.exit(4)
    print(f"[hf] OK -> {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()

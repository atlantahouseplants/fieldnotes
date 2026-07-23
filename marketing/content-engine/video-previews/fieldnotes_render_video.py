
import subprocess
import os
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import textwrap
import json

# --- Configuration ---
SOURCE_IMAGE_PATH = "/home/wallg/fieldnotes/marketing/content-engine/queue/assets/2026-07-22-gate-code-moment.png"
OUTPUT_DIR = "/home/wallg/fieldnotes/marketing/content-engine/video-previews/"
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

VIDEO_WIDTH, VIDEO_HEIGHT = 1080, 1920
FPS = 30
DURATION_PER_IMAGE_BEFORE_FADE = 3.6  # seconds each image is visible before fade starts
FADE_DURATION = 0.5                     # seconds for the fade transition
TOTAL_VIDEO_DURATION = 7.2              # Target ~7.2s (offset2 + F)


# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)

# --- Text Content for each style and its three frames ---
PREVIEW_SPECS = {
    "A": {
        "output_filename": "style-preview-A.mp4",
        "style_type": "lower-third",
        "frames": [
            {
                "headline": "The gate code is in the app.",
                "sub": "He said it once. It's saved forever."
            },
            {
                "headline": "Voice-note the job.",
                "sub": "Drive to the next one."
            },
            {
                "headline": "FieldNotes writes the recap.",
                "sub": "Try it free — fieldnotesapp.io"
            }
        ]
    },
    "B": {
        "output_filename": "style-preview-B.mp4",
        "style_type": "big-center",
        "frames": [
            {
                "headline": "Voice-note the job. Drive to the next one.",
                "sub": "FieldNotes writes the recap."
            },
            {
                "headline": "Try it free — fieldnotesapp.io",
                "sub": "Built for owner-operators."
            },
            {
                "headline": "The gate code is in the app.",
                "sub": "He said it once. It's saved forever."
            }
        ]
    },
    "C": {
        "output_filename": "style-preview-C.mp4",
        "style_type": "kicker",
        "frames": [
            {
                "headline": "Try it free — fieldnotesapp.io",
                "sub": "Built for owner-operators."
            },
            {
                "headline": "The gate code is in the app.",
                "sub": "He said it once. It's saved forever."
            },
            {
                "headline": "Voice-note the job. Drive to the next one.",
                "sub": "FieldNotes writes the recap."
            }
        ]
    }
}

# --- Helper Functions ---
def create_image_with_text(image_path, headline, sub, style_type, style_key, frame_idx):
    """Composes an image with text overlay using PIL based on style."""
    base_image = Image.open(image_path).convert("RGBA")

    # Calculate scaling and cropping for 1080x1920 vertical video
    img_width, img_height = base_image.size
    target_aspect = VIDEO_WIDTH / VIDEO_HEIGHT
    image_aspect = img_width / img_height

    if image_aspect > target_aspect:  # Image is wider than target, crop horizontally
        new_height = VIDEO_HEIGHT
        new_width = int(new_height * image_aspect)
        scaled_image = base_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        left = (new_width - VIDEO_WIDTH) / 2
        top = 0
        right = (new_width + VIDEO_WIDTH) / 2
        bottom = VIDEO_HEIGHT
        final_image = scaled_image.crop((left, top, right, bottom))
    else:  # Image is taller than target, crop vertically
        new_width = VIDEO_WIDTH
        new_height = int(new_width / image_aspect)
        scaled_image = base_image.resize((new_width, new_height), Image.Resampling.LANCZOS)
        left = 0
        top = (new_height - VIDEO_HEIGHT) / 2
        right = VIDEO_WIDTH
        bottom = (new_height + VIDEO_HEIGHT) / 2
        final_image = scaled_image.crop((left, top, right, bottom))

    # Create a drawing object
    draw = ImageDraw.Draw(final_image)

    # Load fonts
    try:
        font_regular = ImageFont.truetype(FONT_PATH, 48)
        font_bold_large = ImageFont.truetype(FONT_BOLD_PATH, 64)
        font_bold_xl = ImageFont.truetype(FONT_BOLD_PATH, 96)
        font_small_caps = ImageFont.truetype(FONT_PATH, 32)
    except IOError:
        print("Warning: Could not load DejaVu fonts. Using default PIL font.")
        font_regular = ImageFont.load_default()
        font_bold_large = ImageFont.load_default()
        font_bold_xl = ImageFont.load_default()
        font_small_caps = ImageFont.load_default()

    if style_type == "lower-third":
        # Dark translucent bar across lower third
        bar_height = int(VIDEO_HEIGHT * 0.25) # 25% of height
        bar_y = VIDEO_HEIGHT - bar_height
        overlay = Image.new('RGBA', (VIDEO_WIDTH, bar_height), (0, 0, 0, int(255 * 0.6)))
        final_image.paste(overlay, (0, bar_y), overlay)

        # Headline
        wrapped_headline = textwrap.fill(headline, width=25) # Adjust width as needed
        bbox = draw.textbbox((0,0), wrapped_headline, font=font_bold_large)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.multiline_text(((VIDEO_WIDTH - text_w) / 2, bar_y + (bar_height * 0.2)), wrapped_headline, font=font_bold_large, fill="white", align="center")

        # Sub-headline
        wrapped_sub = textwrap.fill(sub, width=35)
        bbox = draw.textbbox((0,0), wrapped_sub, font=font_regular)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.multiline_text(((VIDEO_WIDTH - text_w) / 2, bar_y + (bar_height * 0.6)), wrapped_sub, font=font_regular, fill="white", align="center")

    elif style_type == "big-center":
        # Headline
        wrapped_headline = textwrap.fill(headline, width=20) # Adjust width for centering
        bbox = draw.textbbox((0,0), wrapped_headline, font=font_bold_xl)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        
        # Simple stroke for legibility (draw text multiple times slightly offset)
        outline_color = "black"
        fill_color = "white"
        for x_offset in [-2, 0, 2]:
            for y_offset in [-2, 0, 2]:
                draw.multiline_text(((VIDEO_WIDTH - text_w) / 2 + x_offset, (VIDEO_HEIGHT - text_h) / 2 + y_offset), wrapped_headline, font=font_bold_xl, fill=outline_color, align="center")
        draw.multiline_text(((VIDEO_WIDTH - text_w) / 2, (VIDEO_HEIGHT - text_h) / 2), wrapped_headline, font=font_bold_xl, fill=fill_color, align="center")

        # Sub-headline
        wrapped_sub = textwrap.fill(sub, width=35)
        bbox = draw.textbbox((0,0), wrapped_sub, font=font_regular)
        text_w = bbox[2] - bbox[0]
        text_h_sub = bbox[3] - bbox[1]
        draw.multiline_text(((VIDEO_WIDTH - text_w) / 2, (VIDEO_HEIGHT - text_h) / 2 + text_h + 20), wrapped_sub, font=font_regular, fill="white", align="center")

    elif style_type == "kicker":
        # Small all-caps "FIELDNOTES" top-left
        draw.text((50, 50), "FIELDNOTES", font=font_small_caps, fill="white")

        # Headline mid-left
        wrapped_headline = textwrap.fill(headline, width=25)
        bbox = draw.textbbox((0,0), wrapped_headline, font=font_bold_large)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.multiline_text((50, 120), wrapped_headline, font=font_bold_large, fill="white")

        # Short accent underline bar under headline (fixed width)
        draw.rectangle([50, 120 + text_h + 10, 350, 120 + text_h + 15], fill="white")

    temp_png_path = os.path.join(OUTPUT_DIR, f"temp_{style_key}_frame_{frame_idx}.png")
    final_image.save(temp_png_path)
    return temp_png_path

def verify_video(video_path):
    """Verifies the generated MP4 file."""
    print(f"Verifying {video_path}...")
    # Check file size
    if not os.path.exists(video_path) or os.path.getsize(video_path) < 50 * 1024: # 50KB
        print(f"Verification FAILED: {video_path} is missing or too small.")
        return False

    # Check with ffprobe
    ffprobe_command = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration:stream=width,height,codec_name,pix_fmt",
        "-of", "json",
        video_path
    ]
    try:
        result = subprocess.run(ffprobe_command, check=True, capture_output=True, text=True)
        probe_output = json.loads(result.stdout)
            
        format_info = probe_output.get("format", {})
        stream_info = probe_output["streams"][0]

        duration_seconds = float(format_info.get("duration", 0))
            
        print(f"FFprobe Output for {video_path}:\n{json.dumps(probe_output, indent=2)}")

        if not (1070 <= stream_info["width"] <= 1090 and 1910 <= stream_info["height"] <= 1930):
            print(f"Verification FAILED: Resolution is {stream_info['width']}x{stream_info['height']}, expected ~1080x1920.")
            return False
        if not (TOTAL_VIDEO_DURATION - 0.5 <= duration_seconds <= TOTAL_VIDEO_DURATION + 0.5): # ~7.0s +/- 0.5s
            print(f"Verification FAILED: Duration is {duration_seconds:.2f}s, expected ~{TOTAL_VIDEO_DURATION:.2f}s.")
            return False
        if stream_info["codec_name"] != "h264":
            print(f"Verification FAILED: Codec is {stream_info['codec_name']}, expected h264.")
            return False
        if stream_info["pix_fmt"] != "yuv420p":
            print(f"Verification FAILED: Pixel format is {stream_info['pix_fmt']}, expected yuv420p.")
            return False
        print(f"FFprobe OK for {video_path}.")

    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"FFprobe verification FAILED for {video_path}: {e}")
        if isinstance(e, subprocess.CalledProcessError):
            print(f"Stderr: {e.stderr}")
        return False

    # PIL frame check for non-blank
    try:
        frame_extract_path = os.path.join(OUTPUT_DIR, f"temp_frame_check_{os.path.basename(video_path)}.png")
        extract_command = [
            "ffmpeg",
            "-y", # Overwrite output files without asking
            "-ss", str(TOTAL_VIDEO_DURATION / 2), # Extract frame from middle of the video
            "-i", video_path,
            "-vframes", "1",
            frame_extract_path
        ]
        subprocess.run(extract_command, check=True, capture_output=True, text=True)
        
        if not os.path.exists(frame_extract_path):
            print(f"PIL frame check FAILED: Could not extract frame for {video_path}.")
            return False

        frame_image = Image.open(frame_extract_path).convert("L") # Convert to grayscale
        # Calculate standard deviation of pixel values
        pixels = list(frame_image.getdata())
        mean = sum(pixels) / len(pixels)
        variance = sum([(p - mean) ** 2 for p in pixels]) / len(pixels)
        std_dev = variance ** 0.5
        os.remove(frame_extract_path) # Clean up extracted frame

        if std_dev < 10:
            print(f"PIL frame check FAILED: {video_path} frame at middle is likely blank (std_dev={std_dev:.2f}).")
            return False
        print(f"PIL frame check OK for {video_path} (std_dev={std_dev:.2f}).")
        return True

    except Exception as e:
        print(f"PIL frame check FAILED for {video_path}: {e}")
        return False


def main(spec_file=None):
    global SOURCE_IMAGE_PATH
    all_temp_pngs = []
    rendered_video_paths = []
    success_count = 0

    # Optional spec-file mode: render ONE style from a JSON spec
    # {"cards": [{"image","headline","sub"} x3], "style": "A|B|C", "out": "/abs/path.mp4"}
    if spec_file:
        with open(spec_file) as fh:
            s = json.load(fh)
        SOURCE_IMAGE_PATH = s["cards"][0]["image"]
        style_map = {"A": "lower-third", "B": "big-center", "C": "kicker"}
        specs = {s["style"]: {
            "output_filename": os.path.basename(s["out"]),
            "style_type": style_map[s["style"]],
            "frames": [{"headline": c["headline"], "sub": c["sub"]} for c in s["cards"]],
        }}
        out_dir = os.path.dirname(os.path.abspath(s["out"]))
    else:
        specs = PREVIEW_SPECS
        out_dir = OUTPUT_DIR

    for style_key, spec in specs.items():
        print(f"\n--- Rendering Video for Style {style_key} ---")
        temp_png_files_for_style = []
        
        # 1. Generate 3 PIL frames for the current style
        for frame_idx, frame_content in enumerate(spec["frames"]):
            temp_png = create_image_with_text(
                SOURCE_IMAGE_PATH,
                frame_content["headline"],
                frame_content["sub"],
                spec["style_type"],
                style_key,
                frame_idx
            )
            temp_png_files_for_style.append(temp_png)
            all_temp_pngs.append(temp_png)
            print(f"Generated temporary PIL frame: {temp_png}")

        output_video_path = os.path.join(out_dir, spec["output_filename"])
        rendered_video_paths.append(output_video_path)

        # 2. Construct and execute ONE FFmpeg command with xfade
        ffmpeg_command = [
            "/usr/bin/ffmpeg",
            "-y", # Overwrite output files without asking
        ]
        
        # Input arguments for each PNG
        for png_file in temp_png_files_for_style:
            ffmpeg_command.extend([
                "-loop", "1",
                "-t", str(DURATION_PER_IMAGE_BEFORE_FADE),
                "-i", png_file
            ])

        # Filter complex for scaling and xfade
        filter_complex_parts = []
        for i in range(len(temp_png_files_for_style)):
            filter_complex_parts.append(f"[{i}:v] setpts=PTS-STARTPTS, scale={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1:1 [v{i}];")

        # Chain xfades
        if len(temp_png_files_for_style) > 1:
            current_chain_stream = "v0"
            # First xfade offset: DURATION_PER_IMAGE_BEFORE_FADE - FADE_DURATION (3.6 - 0.5 = 3.1)
            offset1 = DURATION_PER_IMAGE_BEFORE_FADE - FADE_DURATION
            # Second xfade offset: (DURATION_PER_IMAGE_BEFORE_FADE - FADE_DURATION) + DURATION_PER_IMAGE_BEFORE_FADE (3.1 + 3.6 = 6.7)
            offset2 = (DURATION_PER_IMAGE_BEFORE_FADE - FADE_DURATION) + DURATION_PER_IMAGE_BEFORE_FADE
            
            # Apply xfade directly to the output streams [v0], [v1], [v2]
            filter_complex_parts.append(
                f"[v0][v1]xfade=transition=fade:duration={FADE_DURATION}:offset={offset1}[vfade1];"
            )
            filter_complex_parts.append(
                f"[vfade1][v2]xfade=transition=fade:duration={FADE_DURATION}:offset={offset2}[outv];"
            )
            final_output_stream_label = "outv"
        else:
            final_output_stream_label = "v0" # Should not happen with 3 frames, but for safety

        ffmpeg_command.extend([
            "-filter_complex", "".join(filter_complex_parts),
            "-map", f"[{final_output_stream_label}]",
            "-c:v", "libx264",
            "-r", str(FPS),
            "-pix_fmt", "yuv420p",
            "-preset", "fast",
            "-movflags", "+faststart",
            output_video_path
        ])
        
        print(f"Running FFmpeg command for {output_video_path}: {' '.join(ffmpeg_command)}")
        try:
            result = subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
            print("FFmpeg stdout:\n", result.stdout)
            if result.stderr:
                print("FFmpeg stderr:\n", result.stderr)

            if verify_video(output_video_path):
                success_count += 1
            else:
                print(f"Verification failed for {output_video_path}. Proceeding to next style.\nFFmpeg Stderr for {output_video_path}:\n{result.stderr}")
        except subprocess.CalledProcessError as e:
            print(f"FFmpeg command FAILED for {output_video_path}:\n{e.stderr}")
            print("Aborting for this style.")

    print("\n--- Overall Summary ---")
    if success_count == len(specs):
        print("All video renders and verifications PASSED!")
    else:
        print(f"{success_count} of {len(specs)} videos rendered and verified successfully. Review above logs for failures.")

    # Clean up temporary PNG files
    for png_file in all_temp_pngs:
        if os.path.exists(png_file):
            os.remove(png_file)
            print(f"Cleaned up {png_file}")

if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)

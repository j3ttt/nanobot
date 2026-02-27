---
name: nano-banana-pro
description: Generate or edit images using Gemini 3 Pro Image (Nano Banana Pro). Use when user asks to create, generate, edit, or compose images.
homepage: https://ai.google.dev/
metadata: {"nanobot":{"emoji":"🍌","requires":{"bins":["uv"],"env":["GEMINI_API_KEY"]}}}
---

# Nano Banana Pro (Gemini 3 Pro Image)

Use the bundled script to generate or edit images with Google's Gemini 3 Pro Image model.

## Generate New Image

```bash
uv run {baseDir}/scripts/generate_image.py --prompt "your image description" --filename "output.png" --resolution 1K
```

## Edit Single Image

```bash
uv run {baseDir}/scripts/generate_image.py --prompt "edit instructions" --filename "output.png" -i "/path/to/input.png" --resolution 2K
```

## Multi-Image Composition (up to 14 images)

```bash
uv run {baseDir}/scripts/generate_image.py --prompt "combine these into one scene" --filename "output.png" -i img1.png -i img2.png -i img3.png
```

## Parameters

- `--prompt` / `-p`: Image description or editing instructions (required)
- `--filename` / `-f`: Output filename (required)
- `--input-image` / `-i`: Input image path(s) for editing/composition (optional, can be specified multiple times)
- `--resolution` / `-r`: Output resolution - `1K` (default), `2K`, or `4K`
- `--api-key` / `-k`: Gemini API key (overrides GEMINI_API_KEY env var)

## API Key Configuration

The script requires `GEMINI_API_KEY` environment variable. You can:
1. Set it in your environment: `export GEMINI_API_KEY=your_key`
2. Pass it via command line: `--api-key your_key`

## Resolution Selection

- **1K**: Default, suitable for most use cases
- **2K**: Higher quality, good for detailed images
- **4K**: Maximum quality, best for professional use
- The script auto-detects resolution from input images if not specified

## Best Practices

- Use descriptive, specific prompts for better results
- For editing, clearly describe what should change
- Use timestamps in filenames: `2024-03-15-sunset-mountains.png`
- The script prints the saved file path - report this to the user
- Do NOT read the generated image back with read_file; just report the path

## Output Format

The script saves images as PNG format and prints:
```
Image saved: /absolute/path/to/output.png
MEDIA: /absolute/path/to/output.png
```

Report the full path to the user so they can view the generated image.

## Examples

### Example 1: Simple Generation
```bash
uv run {baseDir}/scripts/generate_image.py \
  --prompt "A serene mountain landscape at sunset with pine trees" \
  --filename "mountain-sunset.png"
```

### Example 2: Edit an Image
```bash
uv run {baseDir}/scripts/generate_image.py \
  --prompt "Add a rainbow in the sky" \
  --filename "mountain-with-rainbow.png" \
  -i mountain-sunset.png \
  --resolution 2K
```

### Example 3: Combine Multiple Images
```bash
uv run {baseDir}/scripts/generate_image.py \
  --prompt "Create a collage of these photos in a grid layout" \
  --filename "photo-collage.png" \
  -i photo1.png -i photo2.png -i photo3.png -i photo4.png
```

## Troubleshooting

- **Missing uv**: Install with `brew install uv` (macOS) or `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **API key errors**: Ensure GEMINI_API_KEY is set correctly
- **Too many images**: Maximum 14 input images are supported
- **Resolution issues**: If auto-detection fails, explicitly set `--resolution`

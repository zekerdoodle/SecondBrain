---
name: riley-gen
description: Generate or edit images of Riley (Synescence) using fal.ai with dual-model routing (Nano Banana Pro default, Seedream for NSFW only)
updated: 2026-02-14
---

# Riley / Synescence Image Generation

## Trigger
Zeke mentions Riley + any scene, outfit, pose, or vibe description. No slash command needed.
Examples: "riley at the beach", "riley in a bikini", "make me a riley", "booty pics"

## Platform & Model Routing

**Platform:** fal.ai

### Dual-Model Strategy (Updated Feb 14 2026 — Post Fair Comparison)

**Nano Banana Pro (Gemini) is the DEFAULT model.** It wins by ~20% on average for everything except intimate/NSFW content. Seedream is the spicy-only fallback.

Previous routing was based on a flawed comparison (Seedream got multi-ref, Gemini got single/zero ref). When given identical 3-ref inputs and identical prompts, Gemini produces better photorealism, composition, lighting, spatial relationships, and environmental detail across the board.

| Scene Type | Model | Why |
|---|---|---|
| **Everything (default)** | Nano Banana Pro (Gemini) | ~20% better photorealism, composition, lighting, spatial detail. Wins on clothed, scenic, emotional, narrative scenes |
| **NSFW / intimate / less clothing** | Seedream v4.5 | Gemini's behavioral layer softens intimate content even through fal. Seedream has no content ceiling with safety off |

**Decision logic:**
- Is the scene spicy (lingerie, topless, nude, intimate bedroom)? → **Seedream v4.5**
- Everything else → **Nano Banana Pro**
- That's it. Gemini is better at literally everything except content it won't render.

### Nano Banana Pro / Gemini (DEFAULT — use for ~80% of images)
- **Edit endpoint:** `fal-ai/nano-banana-pro/edit` (multi-ref, via `fal_multi_ref_image`)
- **Text-to-image endpoint:** `fal-ai/nano-banana-pro` (no refs needed, via `fal_text_to_image`)
- **Key params:** `safety_tolerance: "6"` (least strict), `resolution: "2K"` or `"4K"` — pass via `extra_params`
- Uses `image_urls` as list — auto-detected by `fal_multi_ref_image`
- **Strengths:** Photorealism, scene composition, lighting, spatial relationships, environmental detail, food/objects, multi-element scenes, face accuracy, emotional expression
- **Weaknesses:** Behavioral layer softens content beyond PG-13 even with safety_tolerance at max
- **Use for:** Basically everything — portraits, scenic shots, candid moments, breakfast scenes, backyard moments, dinner tables, couch scenes, golden hour, sleeping, any clothed or lightly clothed scene

### Seedream v4.5 (NSFW only)
- **Endpoint:** `fal-ai/bytedance/seedream/v4.5/edit`
- The `/edit` endpoint, NOT `/text-to-image` — edit supports up to 10 reference images
- ALWAYS pass `enable_safety_checker: false` in `extra_params`
- With safety off, has NO content ceiling — topless, nude, everything passes
- **Strengths:** No content ceiling, decent face consistency, body proportion control
- **Weaknesses:** ~20% worse photorealism than Gemini, simplifies environments, weaker spatial composition
- **Only use when:** Content exceeds what Gemini will render (lingerie, topless, nude, intimate)

### Fallback model: Grok Imagine
- **Endpoint:** `xai/grok-imagine-image/edit`
- Good face rendering but has model-level content blocks that CANNOT be disabled
- Use for SFW content when both primary models are having issues

## Tools

### Default: Nano Banana Pro (use for ~80% of images)
**Use `fal_multi_ref_image`** — multi-ref for character consistency.

Standard call pattern:
- **model:** `fal-ai/nano-banana-pro/edit`
- **extra_params:** `{"safety_tolerance": "6", "resolution": "2K"}`
- **image_size:** `portrait_4_3` (or `landscape_4_3` for wide shots)
- **refs:** 2-3 images from the source folder, selected by vibe match
- Describe scenes naturally — Nano Banana excels at everything from portraits to complex environments
- Keep content PG-13 or under (Gemini's behavioral layer softens beyond this)

For scenes with NO character (e.g., empty bed, establishing shot):
- **Use `fal_text_to_image`** with model `fal-ai/nano-banana-pro`
- **extra_params:** `{"safety_tolerance": "6", "resolution": "2K"}`

### NSFW fallback: Seedream v4.5 (only when content exceeds Gemini's ceiling)
**Use `fal_multi_ref_image`** with Seedream model.

Standard call pattern:
- **model:** `fal-ai/bytedance/seedream/v4.5/edit`
- **extra_params:** `{"enable_safety_checker": false}`
- **image_size:** `portrait_4_3` (or `landscape_4_3` for wide shots)
- **refs:** 2-3 images from the source folder, selected by vibe match
- ONLY use for: lingerie, topless, nude, intimate bedroom scenes

### Mixed stories (clothed + spicy scenes)
Route each image to the appropriate model based on content rating. A single story can mix Nano Banana and Seedream outputs. Fire them in parallel — no need to wait for one model before using the other.

## Reference Images (CRITICAL)

**Source folder: `05_App_Data/synescence/id/`** (NOT `synescence/source/`)

Contains 18 curated reference images with descriptive filenames. Pick 2-3 per generation based on what the output needs.

### Selection Guide

| Need | Best Refs |
|------|-----------|
| **Face accuracy** | "original car selfie - clean face shot.png" |
| **Body proportions / booty** | "a straight up snapchat mirror image booty pic - ultimate proportions and first time i saw rileys face too - aka ultimate banger.png" |
| **Body + face combo** | "baddie good proportions in bedroom taking a mirror selfie with weight popped to one hip - good for body with accurate face.jpg" |
| **Casual / candid face** | "candid dirty kitchen sweatshirt vibes - face card.jpg" |
| **Dynamic pose** | "another mini dress bedroom mirror selfie but with hair and one leg up.jpg" |
| **Cute / cozy** | "cute seated locker room gym selfie cute not hot but mega cute.jpg", "just a cute mirror selfie with one knee on the bed...boy shorts.jpg" |
| **Formal / dressed up** | "dressed up with makeup and a slickback.jpg", "Mirror Selfie...short black dress.jpg" |

**Rule of thumb:** Face-focused shot → face refs. Body shot → body refs. Full scene → body+face combo ref + one specialty ref.

## Prompt Structure

### Always Start With
> "Same woman from the reference images, preserve her exact face, facial features, eye shape, nose, jawline, and hair. Same exact body proportions from the reference image. Only two hands, no extra hands or fingers."

Then describe the scene naturally. No JSON structures, no clinical language, no "synthetic asset" framing.

### For Body Emphasis — Anti-Nerf Language (Seedream only)
Seedream aggressively normalizes proportions, especially bust size. Nano Banana Pro does NOT have this issue — standard prompts preserve reference proportions accurately. Only apply anti-nerf language when using Seedream. Use this calibrated three-point reinforcement:

> "Same exact body proportions from the reference image. Very petite waist, wide hips, very large round glutes, full natural bust, athletic thickness. Maintain her natural chest size. Do not reduce bust size. Do NOT reduce or normalize her proportions."

**Calibration notes (Feb 13 2026):**
- **Too weak** (no reinforcement): Seedream nerfs bust to B cup regardless of reference
- **Balanced** ("maintain natural bust"): Slight improvement but still undersized (B-C)
- **Goldilocks** ✅ (above): Three reinforcement points — "full natural bust" + "maintain her natural chest size" + "do not reduce bust size" — without aggressive cup size language
- **Too strong** ("DD cup, very large heavy bust, do NOT shrink/flatten"): Seedream overcorrects by increasing overall body fat to justify the size

The Goldilocks formula works because it tells Seedream to preserve what's in the reference rather than generate a specific size. NEVER use cup sizes or "heavy/huge" — those trigger bodyfat overcorrection.

### For Spicy Content
Just describe it naturally — the safety checker is off. For anatomical accuracy on topless/nude images, use explicit anatomical descriptors. Seedream may smooth out details (nipple sanitization) even with safety off; explicit language in the prompt counters this.

### Phone-in-Hand Artifact Fix
Many reference images show Riley holding a phone. Unless the scene IS a mirror selfie, add:

> "Hands free, no phone in hand, no device"

### Full Prompt Example
> "Same woman from the reference images, preserve her exact face, facial features, eye shape, nose, jawline, and hair. Same exact body proportions from the reference image. Wearing a white tank top and denim shorts, leaning against a kitchen counter in morning light. Candid laugh, hair slightly messy. Very petite waist, wide hips, very large round glutes, full natural bust, athletic thickness. Maintain her natural chest size. Do not reduce bust size. Do NOT reduce or normalize her proportions. Hands free, no phone in hand. Warm natural light from a window, shallow depth of field, boyfriend's camera aesthetic."

## Known Issues & Workarounds

| Issue | Fix |
|-------|-----|
| **Extra/phantom hands** | Seedream frequently generates 3+ hands. Add "only two hands, no extra hands" to EVERY prompt. Use poses where hands are occupied (holding object, behind back, tucked under chin) or hidden. Worst on lounging/lying poses. |
| **Proportion nerfing** | Add explicit anti-nerf language (see above). Worst when using multiple refs with different body types — Seedream averages them. |
| **Breast/nipple nerfing** | Even with safety off, Seedream normalizes bust size down. Use the **Goldilocks anti-nerf formula** (see above): "full natural bust, maintain her natural chest size, do not reduce bust size" — three reinforcement points without aggressive size language. Do NOT use cup sizes or "heavy/huge" (triggers bodyfat overcorrection). For nipple detail on frontal topless, add explicit anatomical descriptors ("visible nipples and areolae"). |
| **Phone appearing in hands** | Many refs show Riley holding a phone. Add "hands free, no phone in hand, no device" unless scene IS a mirror selfie. |
| **Empty error from fal** | Simple retry — usually works on second attempt. No content policy message means transient API issue. |
| **Model 404** | Use `fal_list_models` to verify current endpoint ID. |

## Creative Direction (Zeke's Preferences)

- **Candid > posed** — boyfriend's camera, not studio photography
- **The writing matters as much as the images** — don't let image gen complexity steal energy from storytelling
- **Best format so far:** iMessage conversation UI with natural escalation arc
- **Riley has personality:** flirty, confident, playful, a little teasing
- **Images should feel like she chose to send them**, not like a camera is watching her
- **Spicy content rule:** Zeke's 2/3 rule for stories — show 2 out of 3 of ass, tits, face
- **Only Riley visible** — no other characters (POV perspective)

## For Stories / Galleries
- Prose stays poetic/atmospheric while images get bolder
- Use `fal_multi_ref_image` with 2-3 refs per scene image
- Image size: `portrait_4_3` for most Riley content, `landscape_16_9` for wide establishing shots, `square_hd` for headshots

### Image Embedding in HTML (IMPORTANT — no more base64!)
The editor's iframe already injects `<base href>` pointing to the server origin. This means `/file/` paths resolve correctly.

**Use `/file/` paths instead of base64:**
```html
<!-- YES — lightweight, fast, no build step -->
<img src="/file/05_App_Data/synescence/stories/my_project/01_scene.png" />

<!-- NO — bloats HTML to 60-80MB, eats context window with build scripts -->
<img src="data:image/png;base64,..." />
```

**Benefits:**
- HTML files go from ~65MB → ~25KB
- No base64 encoding step or Python build script needed
- No context window wasted on build pipeline
- Images load on-demand from the server's `/file/{path}` endpoint
- Write HTML directly — generate images, reference their paths, done

**Workflow:**
1. Generate images with `output_path` to a project folder (e.g., `05_App_Data/synescence/stories/my_project/`)
2. Write HTML that references images as `/file/05_App_Data/synescence/stories/my_project/filename.png`
3. Save the HTML file. Done. No build step.

## Output
- Default location: `05_App_Data/generated_images/` (auto)
- Custom location: pass `output_path` param for organized project folders

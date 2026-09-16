---
name: ai-sprite-workflow
description: Process and review AI-generated sprite sheets or animation folders with the local F:\game-tool workflow, including background removal, intelligent frame detection, mask repair, aligned-frame export, and user-requested asset-history cleanup. Do not use for generating or manually drawing source artwork.
---

# AI Sprite Workflow

Use the bundled client instead of reconstructing API calls. It starts the local service when needed and prints compact JSON suitable for further tool use.

```powershell
python scripts/sprite_workflow_client.py process <image> [--expected-frames N] [--output <directory>]
```

Use `import-folder` when the requested operation is to register a complete animation folder without analyzing it. Use `process-folder` for import plus batch analysis/export instead of manually rebuilding a loop. Use `library` and `trash` only for local asset/history management.

## Operating rules

- Prefer `--expected-frames` when the generation request or surrounding workflow already knows the count. Keep rows and columns at zero unless they are genuinely known layout hints.
- Do not grid-crop the source before this workflow. The engine removes the background from the whole image, refines enclosed background holes and pale edge contamination, identifies frame anchors, and assigns detached effects or props.
- Mask refinement defaults to `conservative` to clean canvas-coloured edge residue while retaining semantic foreground protection. Use `off` when the initial remover output must be preserved exactly, step up to `balanced` only after inspecting residue, and reserve `strong` for visible canvas-coloured background. Never increase tolerance blindly: compare `initial_cutout_path` and `refined_cutout_path`.
- Adaptive chroma cleanup is enabled by default. It activates only for a uniform, saturated green or blue screen, removes high-confidence enclosed key-colour holes that border flood-fill cannot reach, and de-spills surviving antialiased edges. Keep `--chroma-strength standard` for normal work, use `strong` for heavy spill, and use `--no-smart-chroma` when the character intentionally contains colours indistinguishable from the screen. Ordinary white or illustrated backgrounds do not activate this stage.
- If automatic refinement leaves an enclosed pocket, visually identify a pixel coordinate and use `mask-point ... background`. Use `foreground` only to restore an evidenced mistaken removal. Point edits operate on one colour-connected region and do not rerun the model.
- If strong refinement removes a multi-colour feature, prefer `restore-region` over multiple foreground clicks. Supply a polygon in source-image coordinates; only that polygon is blended from the preserved initial cutout, while the rest of the refined mask remains unchanged. Inspect both cutouts before choosing the polygon and keep it tight around the evidenced damage.
- If global strong refinement still leaves background, use `refine-region`. It samples any detected chroma screen from the full source, clears enclosed key-colour pixels only inside the polygon, then runs the configured local remover and evidence gate. Start with `standard`; higher strengths broaden background evidence without treating the whole polygon as an eraser.
- Source images already under `F:\game-tool` can be processed directly. For a user-provided image elsewhere, add `--import-external`; this copies it into `F:\game-tool\input` without modifying the original.
- For an animation directory, `import-folder <folder>` is the explicit whole-folder import interface. It preserves nested relative paths under `input/<collection>`, reuses identical existing files, and reports every imported project path. Use `--collection <name>` to override the source folder name and `--overwrite` only when replacing conflicting paths is intended. The direct HTTP equivalent is `POST /api/v1/import-folder`.
- For a character animation set containing multiple sheets, use `process-folder`. It recursively processes supported images in stable relative-path order, preserves an explicitly imported external folder below `input`, and returns per-image failures and review counts. Use `--skip-existing` when resuming an interrupted or previously processed set. Exports preserve the collection/subfolder structure below `output`, so repeated names from different actions do not collide.
- Export settings are persistent per source image, not global and not tied to a job ID. When `process` or `export` explicitly receives `--output` and/or an artifact selection, the service records them for that source path. A later re-analysis of the same path must omit the flags when it should inherit those settings; do not copy the destination from an unrelated recent job. A source with no record defaults to the canonical project output and `frames_aligned`. Animation collections keep a separate group profile; each image exported through a custom group destination also remembers its exact delivered subdirectory.
- Use `--output <absolute-or-relative-directory>` to deliver directly into the caller's target instead of copying afterward. Use `--artifact trimmed`, `--artifact sheet`, `--artifact masks`, or repeat `--artifact` for a selective bundle. Use `--all` only when the caller needs aligned frames, trimmed frames, masks, Sprite Sheet, and manifest together.
- A custom single-image destination receives the selected frames directly. A custom folder/collection destination preserves each source's relative stem as a subdirectory, preventing `frame_001.png` from different actions from colliding. Read `delivery_dir` in the JSON response as the user-facing result location; `output_dir` remains the canonical project export cache.
- Commands that create or export results through this client persist the `ai` tag on the job. Those jobs appear in the GUI's “AI处理记录” tab for focused human review and can be reopened for mask or assignment edits. Folder jobs are grouped by the first directory below `input` in both AI records and processing history; legacy jobs recover the same collection from `source_path`, while standalone images remain in a separate single-image section. Do not remove that tag merely because a later manual edit occurs.
- After reviewing or locally repairing a processed animation set, use `export-folder <collection>` to export every ready job from its latest saved state. By default it reports and skips jobs that still need component review; use `--include-review` only when the user has explicitly accepted those unresolved results. Each export also places a flat, renamed copy of its final Sprite Sheet under `output/<collection>/result` for quick visual review.
- Treat `review_count > 0` as a successful analysis that still needs visual verification. Inspect the returned `overlay_path` before reassigning components. Never guess an assignment from component IDs alone.
- Use `assign` only when the overlay or user instruction supplies evidence. Frame `0` means ignore. Main anchor components cannot be moved or ignored.
- Use `library` to inspect normal input assets or processing history. Test samples are intentionally omitted from its source listing.
- Treat library cleanup as a separate, explicitly authorized action. First run `trash` without `--confirm` and report the exact plan. Add `--confirm` only after the user has clearly requested those exact sources or jobs be removed. Cleanup is recoverable under `workspace/trash`, related jobs are included by default, and test samples plus exported assets remain protected. Do not infer cleanup permission from a processing request.
- Report the output directory and whether review remains. Do not claim a job is fully verified merely because export succeeded.
- Report `delivery_dir` rather than asking the user to run a follow-up copy script.

## Commands

```powershell
# Analyze and export
python scripts/sprite_workflow_client.py process F:\game-tool\input\sheet.png --expected-frames 6 --output F:\godot-game\assets\hero\attack

# Reprocess the same source and reuse its own remembered destination/artifacts
python scripts/sprite_workflow_client.py process F:\game-tool\input\sheet.png --expected-frames 6

# Analyze without exporting
python scripts/sprite_workflow_client.py process F:\game-tool\input\sheet.png --analyze-only

# Adaptive green/blue-screen cleanup is on by default; override only when needed
python scripts/sprite_workflow_client.py process F:\game-tool\input\green-screen.png --chroma-strength strong
python scripts/sprite_workflow_client.py process F:\game-tool\input\green-character.png --no-smart-chroma

# Process every image in an animation folder and resume safely
python scripts/sprite_workflow_client.py process-folder F:\game-tool\input\hero-attack --expected-frames 6 --skip-existing --output F:\godot-game\assets\hero

# Import a complete folder without analyzing it yet
python scripts/sprite_workflow_client.py import-folder F:\generated\hero-attack --collection hero-attack

# Re-run only mask refinement from the preserved initial cutout
python scripts/sprite_workflow_client.py refine <job-id> --mode balanced --tolerance 18 --matte-width 3

# Clear one enclosed background pocket at source-image coordinate x=420, y=180
python scripts/sprite_workflow_client.py mask-point <job-id> 420 180 background

# Restore an accidentally removed multi-colour feature from the initial cutout
python scripts/sprite_workflow_client.py restore-region <job-id> "365,35;495,35;525,150;345,150" --feather 4

# Re-run original background removal only in a polygon with selectable strength
python scripts/sprite_workflow_client.py refine-region <job-id> "365,35;495,35;525,150;345,150" --strength strong --feather 4

# Inspect the review queue and local overlay path
python scripts/sprite_workflow_client.py inspect <job-id>

# Assign one detached component to a frame, or ignore it with frame 0
python scripts/sprite_workflow_client.py assign <job-id> <component-id> <frame-id>

# Export a reviewed job
python scripts/sprite_workflow_client.py export <job-id> --output F:\godot-game\assets\hero\attack

# Deliver the complete processed bundle instead of the default aligned frames
python scripts/sprite_workflow_client.py export <job-id> --output F:\archive\hero-attack --all

# Export all ready jobs in one animation collection from their latest edited state
python scripts/sprite_workflow_client.py export-folder hero-attack --output F:\godot-game\assets\hero

# List recent jobs
python scripts/sprite_workflow_client.py jobs

# List normal input assets and processing history (test samples are hidden)
python scripts/sprite_workflow_client.py library
python scripts/sprite_workflow_client.py library --kind ai

# Preview a recoverable cleanup; repeat with --confirm only after explicit authorization
python scripts/sprite_workflow_client.py trash --source "input/hero-attack/frame_01.png"
```

For interactive review, open `http://127.0.0.1:7865`; the GUI provides a unified image/folder import entry, animation-set navigation, multi-select history management, and a dedicated AI processing record. Single and group export dialogs accept a remembered destination plus an artifact preset; output-folder actions open a new foreground Explorer window. Read [references/api.md](references/api.md) only when implementing a direct integration, automating cleanup, or diagnosing the service contract.

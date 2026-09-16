# Local API contract

Default base URL: `http://127.0.0.1:7865`

The bundled client is the preferred integration surface. `import-folder` calls the explicit whole-folder import endpoint. `process-folder` then processes images in stable relative-path order, while `library` combines `/api/sources` and `/api/jobs`. Direct callers should reproduce those semantics only when the client cannot be used.

## Animation-folder import

`POST /api/v1/import-folder`

```json
{
  "folder_path": "F:\\generated\\hero-attack",
  "collection": "hero-attack",
  "overwrite": false
}
```

This local-only endpoint imports all supported images recursively into `input/<collection>` and preserves nested relative paths. `collection` is optional and defaults to the source folder name. Identical existing files are reused; conflicting names receive a numeric suffix unless `overwrite` is true. A folder already below `input` is registered without copying. The response includes `collection`, `imported_root`, counts, and a stable `sources` array whose `source_path` values can be passed directly to `POST /api/v1/process`.

## Processing

`POST /api/v1/process`

```json
{
  "source_path": "F:\\game-tool\\input\\sheet.png",
  "expected_frames": 6,
  "rows": 0,
  "columns": 0,
  "background_mode": "auto",
  "background_model": "isnet-anime",
  "provider": "auto",
  "refine_mode": "conservative",
  "refine_tolerance": 18,
  "matte_width": 3,
  "smart_chroma_enabled": true,
  "smart_chroma_strength": "standard",
  "export": true,
  "output_path": "F:\\godot-game\\assets\\hero\\attack",
  "artifacts": ["aligned"],
  "ai_tag": true
}
```

The response contains `job` and an optional `export`. Important job fields are:

- `id`: persistent job ID.
- `layout.frame_count`, `layout.rows`, `layout.columns`.
- `status`: `ready` or `needs_review`.
- `review_component_ids`: detached components below the confidence threshold.
- `frames`: frame IDs, bounding boxes, anchor component IDs, and confidence.
- `refinement`: removed pixel count, enclosed background count, feathered pixel count, and whether semantic protection was used.
- `refinement.adaptive_chroma`: whether a green/blue screen was detected, the sampled key colour, enclosed region count, removed pixels, softened pixels, and de-spilled pixels.
- `files.initial_cutout`, `files.cutout`: before/after refinement images.
- `tags`: contains `ai` when the bundled AI client created or exported the job.
- `export_profile`: the destination and artifact selection remembered for this exact `source_path`, or `null` before its first export. It is independent of the current job ID.

`output_path` and `artifacts` are optional overrides. When either field is omitted, the service restores that field from the source image's persistent export profile. If the source has no profile, it uses the canonical project output and `aligned`. Sending `output_path: ""` explicitly resets that source to the canonical project destination. A successful export updates `workspace/export-profiles.json`; therefore a newly analyzed job for the same source inherits the earlier destination.

Adaptive chroma cleanup defaults to `smart_chroma_enabled: true` and only activates after detecting a uniform saturated green or blue border. `smart_chroma_strength` accepts `conservative`, `standard`, or `strong`. Set the boolean to false when an intentional foreground colour is indistinguishable from the screen.

## Review and export

- `GET /api/jobs/{job_id}` returns current job state.
- `POST /api/jobs/{job_id}/assign` with `{"component_id": 12, "frame_id": 3}` changes an assignment. Frame `0` ignores a non-anchor component.
- `POST /api/jobs/{job_id}/refine` with `{"mode":"balanced","tolerance":18,"matte_width":3,"smart_chroma_enabled":true,"smart_chroma_strength":"standard"}` rebuilds the mask from the preserved initial cutout without rerunning segmentation. Omit the chroma fields to preserve the job's current setting.
- `POST /api/jobs/{job_id}/mask-point` with `{"x":420,"y":180,"action":"background","tolerance":18,"feather":2}` edits one colour-connected region. Action can be `background` or `foreground`.
- `POST /api/jobs/{job_id}/mask-region` with `{"points":[[365,35],[495,35],[525,150],[345,150]],"feather":4}` restores the preserved initial cutout only inside a source-coordinate polygon. Use this for multi-colour details removed by strong refinement.
- `POST /api/jobs/{job_id}/refine-region` with `{"points":[[365,35],[495,35],[525,150],[345,150]],"strength":"strong","tolerance":18,"matte_width":3,"feather":4}` reruns the job's configured background remover on a contextual crop. The crop model only proposes removals; full-image canvas colour, local flatness, and semantic foreground confidence gate every write-back, which remains limited to the polygon. Strength is `gentle`, `standard`, `strong`, or `maximum`.
- `POST /api/jobs/{job_id}/export` accepts `{"output_path":"F:\\target","artifacts":["aligned"],"ai_tag":true}`. Artifact names are `aligned`, `trimmed`, `masks`, `sheet`, and `manifest`. Omitted fields inherit this source's profile; a first-time source defaults to aligned only. The response's `delivery_dir` is the caller-facing destination, while `output_dir` is the canonical project cache.
- `POST /api/jobs/{job_id}/open-output` opens the exported job directory in a new foreground Windows Explorer window. It returns `404` until that job has been exported; the response includes `foreground` to report whether Windows accepted the focus request.
- `POST /api/collections/export` accepts `{"collection":"hero-attack","include_review":false,"output_path":"F:\\target","artifacts":["aligned"],"ai_tag":true}` and exports the latest saved state of every analyzed image. Omitted export fields inherit that collection's own profile. A custom destination preserves relative source stems as directories and records each image's exact delivered subdirectory as its source profile. It reports exported, missing, review-skipped, and failed items separately, and maintains a flat `result` preview folder.
- `GET /api/collections/{collection}/export-profile` returns the collection-specific remembered destination and artifact selection for GUI prefill.
- `POST /api/collections/open-output` with `{"collection":"hero-attack","view":"result","output_path":"F:\\target"}` opens the concentrated preview directory in a new foreground Explorer window. Set `view` to `root` for the full delivery tree.
- `POST /api/dialogs/select-output-folder` with `{"initial_path":"F:\\target"}` opens a foreground native Windows folder picker for GUI use.
- `GET /api/jobs` lists saved jobs. Every summary includes `collection` and `relative_name`, derived from its source below `input`; this also restores animation-set membership for legacy jobs that predate explicit collection metadata.
- `GET /api/health` reports engine and GPU availability.

## Library management

- `GET /api/sources` lists both test and input images. Normal asset-management callers must filter to `group == "输入队列"`; the bundled `library` command already does this.
- `GET /api/jobs` lists processing history, including `tags`, `collection`, `relative_name`, and the most recent `last_export`. Filter jobs whose tags contain `ai` for the AI processing record, then group non-empty `collection` values as animation sets and keep empty values in a standalone-image section.

`POST /api/library/trash` moves selected input assets and job histories into a recoverable project trash batch:

```json
{
  "source_paths": ["input/hero/attack.png"],
  "job_ids": ["optional-job-id"],
  "include_related_jobs": true
}
```

Only files under `input` and job directories under `workspace/jobs` are accepted. Test samples are protected, exported assets are retained, and moved content plus a manifest are stored under `workspace/trash/<timestamp-batch>`.

The bundled `trash` command is plan-only unless `--confirm` is supplied. Keep that two-step behavior in AI-driven workflows and require an explicit cleanup request before confirmation.

Job working files are stored under `F:\game-tool\workspace\jobs\<job_id>`. Canonical exports remain under `F:\game-tool\output`; when `output_path` is provided, selected deliverables are also written directly to that destination.

The API deliberately accepts only images inside `F:\game-tool`. Import external user files into `input` before calling it.

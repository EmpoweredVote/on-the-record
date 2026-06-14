# VibeVoice GPU Cost Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the same long VibeVoice meeting concurrently on L40S, A100-40GB, and A100-80GB and save reproducible cost/performance results.

**Architecture:** Extract a GPU-sweep inference helper in `bench/modal_app.py`, expose one Modal function per fixed GPU type, and add a local concurrent runner that renders JSON and Markdown. Keep production VibeVoice behavior unchanged and isolate sweep artifacts by GPU key.

**Tech Stack:** Python, Modal, PyTorch CUDA metrics, concurrent.futures, pytest.

---

### Task 1: Pure sweep helpers

**Files:**
- Create: `bench/gpu_sweep.py`
- Create: `tests/test_vibevoice_gpu_sweep.py`

- [ ] **Step 1: Write failing helper tests**

Test that `estimate_gpu_cost`, `gpu_key`, and `render_markdown` produce
stable values for the three requested GPUs and include failures in the
report.

- [ ] **Step 2: Verify RED**

Run:

```bash
python -m pytest tests/test_vibevoice_gpu_sweep.py -q
```

Expected: FAIL because `bench.gpu_sweep` does not exist.

- [ ] **Step 3: Implement minimal helpers**

Define current per-second Modal prices, normalize GPU labels, calculate
cost, and render a compact Markdown comparison.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python -m pytest tests/test_vibevoice_gpu_sweep.py -q
```

Expected: PASS.

### Task 2: GPU-specific Modal inference

**Files:**
- Modify: `bench/modal_app.py`
- Modify: `tests/test_vibevoice_gpu_sweep.py`

- [ ] **Step 1: Write failing dispatch-contract tests**

Assert that all three fixed functions exist and use distinct result keys.

- [ ] **Step 2: Verify RED**

Run the focused test and confirm the functions are missing.

- [ ] **Step 3: Implement the inference helper and wrappers**

Reuse pinned VibeVoice loading/parsing, reset CUDA peak statistics before
generation, record peak allocated/reserved bytes, and persist one manifest
per requested GPU. Add wrappers:

```python
vibevoice_sweep_l40s
vibevoice_sweep_a100_40gb
vibevoice_sweep_a100_80gb
```

- [ ] **Step 4: Verify GREEN and import**

Run the focused tests and import `bench.modal_app`.

### Task 3: Concurrent runner and saved report

**Files:**
- Create: `bench/run_vibevoice_gpu_sweep.py`
- Modify: `tests/test_vibevoice_gpu_sweep.py`

- [ ] **Step 1: Write failing concurrency test**

Use fake remote functions with a synchronization barrier and assert all
three are active before any completes.

- [ ] **Step 2: Verify RED**

Run the focused test and confirm the runner is missing.

- [ ] **Step 3: Implement concurrent dispatch**

Use `ThreadPoolExecutor(max_workers=3)` inside one `app.app.run()` context.
Always write `results.json` and `README.md`, including failed GPU results.

- [ ] **Step 4: Verify GREEN**

Run the focused test suite.

### Task 4: Execute and verify

**Files:**
- Create: `bench/results/vibevoice-gpu-sweep-2026-06-14/results.json`
- Create: `bench/results/vibevoice-gpu-sweep-2026-06-14/README.md`

- [ ] **Step 1: Run all three GPUs concurrently**

```bash
python bench/run_vibevoice_gpu_sweep.py \
  --meeting-id 2026-02-04-council \
  --output-dir bench/results/vibevoice-gpu-sweep-2026-06-14
```

- [ ] **Step 2: Inspect measurements**

Confirm successful results include actual GPU, peak VRAM, runtime, cost,
turn counts, and stitching match counts. Confirm failed results include a
useful error.

- [ ] **Step 3: Run complete verification**

```bash
python -m pytest -q
python -m py_compile bench/modal_app.py bench/gpu_sweep.py \
  bench/run_vibevoice_gpu_sweep.py
git diff --check
```

- [ ] **Step 4: Check repository safety**

Verify no `.env`, credentials, audio, model weights, or raw generated
transcripts are included in the diff.

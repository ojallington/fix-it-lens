# SAM 3.1 open weights, local

The `local` mode's SAM: the hosted sam-3.1 request in, the hosted event stream out, so `calls/` parses both the
same way. It runs on the GPU host, binds on its tailnet address only, and holds no key.

It is never used beside Glimmer: the two do not fit on one 32 GB GPU at once (SAM holds 11.2 GB after a clip,
measured with nvidia-smi). A local client sends it nothing unless `FIXIT_LOCAL_SAM=1` (eval, or a
configuration without Glimmer), the warm-up loads Glimmer only, and /preflight reads
"Local SAM 3.1: on demand, not used beside Glimmer". So local mode's second witness is a separate Glimmer locate
plus optical flow on the server, and the verdict says so ("Checked without SAM (Glimmer and motion tracking)").

## Bringing it up (once facebook/sam3.1 access is approved)

Access to the gated `facebook/sam3.1` repo is reviewed by Meta by hand. Once approved, a Hugging Face read token goes
in `~/.config/fleet/hf-read.token` (0600) on the app host. The GPU host is the host of `FIXIT_LOCAL_SAM_URL`
(else `config/models.toml` `[backend.local] sam_url`, which defaults to localhost); `SAM_HOST` overrides the ssh
target. Then, from the repo root:

1. `local/sam_server/deploy/stage.sh env`: copies the code to `/opt/fixit-sam` on the GPU host and builds its own
   uv env (Python 3.12, torch cu128, sam3 from source). No GPU, nothing started.
2. `stage.sh fetch`: the token goes over scp (never in argv) to `/root/.config/fleet/hf-read.token`. The weights
   download to `/opt/fixit-sam/weights`, and the checkpoint's sha256 must match the hub's LFS etag
   (`verified.json`). A copy of that record lands in `~/.cache/fixit/sam3.1/` on the app host, which is what flips
   /preflight from "access pending" to the server's real state.
3. `stage.sh service`: installs `deploy/fixit-sam.service` (a system unit) and starts it. `GET /health` must say
   `"weights": true`.
4. `stage.sh measure`: one cold and one warm call through `calls/`, and the MiB the process holds, so the GPU's
   residency register has a measured number before SAM counts as resident.

The model loads on the first request. After `SAM_IDLE_S` (900 s in the unit) of idle the process exits and systemd
starts it again unloaded, so it holds nothing on the shared GPU between sessions (unloading in-process left 5.9 GB
held).

`config/models.toml` `[backend.local] sam_url` points `calls/` at it. SAM 3.1 is under Meta's SAM License (not
Apache-2.0), used only in self-hosted local mode; the weights are never bundled or committed.

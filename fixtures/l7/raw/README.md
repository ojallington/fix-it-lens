# Raw Glimmer streams

ollama's OpenAI-door SSE (`/v1/chat/completions`, `stream: true`), as `muse-glimmer:30b` sent it, for the local
backend's own tests (the translation to Responses events and the `<|eot|>` strip). Responses only: no request body,
no frame, no manual text. The Responses-shaped fixtures the client replays are the `<sha>.jsonl` files one level up.

- `judge-eot.sse`: a JSON-schema step check whose content ends in the leaked `<|eot|>` token. Content, usage and
  model are those of a probe on the GPU host (ollama 0.34.0, `reasoning_effort: "none"`); the chunk framing
  follows the door's streamed shape measured the same night.
- `guide-tool.sse`: a streamed `segment` tool call in the measured framing and usage; the arguments value is
  reconstructed, because the probe printed that line truncated.

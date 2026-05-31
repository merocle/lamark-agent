# Lamark vLLM benchmark — stage3-dflash-tuned

- date: 2026-05-27 16:52:08
- endpoint: http://localhost:8000/v1
- model: qwen-base
- model root: /lamark/models/hf/Qwen_Qwen3.6-35B-A3B-FP8
- max_model_len: 131072

## Throughput (3 runs each, 512 tokens out)

| Prompt | Prompt tok | Compl tok | Wall avg | **tok/s avg** | tok/s peak |
|---|---|---|---|---|---|
| short | 750 | 512 | 24.30s | **31.3** | 41.9 |
| medium | 837 | 512 | 9.64s | **53.2** | 55.7 |
| long | 1066 | 512 | 11.10s | **46.1** | 46.5 |

**Overall avg: 43.6 tok/s  (peak 55.7)**

## Time to first token

| Prompt | TTFT |
|---|---|
| short | 311ms |
| medium | 295ms |
| long | 376ms |

## Raw runs

```json
[
  {
    "prompt": "short",
    "runs": [
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 48.355650186538696,
        "tok_per_s": 10.588214573165457
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 12.330379009246826,
        "tok_per_s": 41.52346003444337
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 12.210106134414673,
        "tok_per_s": 41.93247743825154
      }
    ]
  },
  {
    "prompt": "medium",
    "runs": [
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 9.18873906135559,
        "tok_per_s": 55.720376493580176
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 10.305657148361206,
        "tok_per_s": 49.68145093798484
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 9.433399200439453,
        "tok_per_s": 54.27523940428055
      }
    ]
  },
  {
    "prompt": "long",
    "runs": [
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 11.232608795166016,
        "tok_per_s": 45.58157497840934
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 11.037842035293579,
        "tok_per_s": 46.38587854064919
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 11.021811962127686,
        "tok_per_s": 46.4533419513321
      }
    ]
  }
]
```

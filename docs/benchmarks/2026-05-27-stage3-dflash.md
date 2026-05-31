# Lamark vLLM benchmark — stage3-dflash

- date: 2026-05-27 16:40:23
- endpoint: http://localhost:8000/v1
- model: qwen-base
- model root: /lamark/models/hf/Qwen_Qwen3.6-35B-A3B-FP8
- max_model_len: 131072

## Throughput (3 runs each, 512 tokens out)

| Prompt | Prompt tok | Compl tok | Wall avg | **tok/s avg** | tok/s peak |
|---|---|---|---|---|---|
| short | 750 | 512 | 21.91s | **33.3** | 44.4 |
| medium | 837 | 512 | 9.59s | **53.7** | 58.0 |
| long | 1066 | 416 | 8.93s | **46.5** | 50.0 |

**Overall avg: 44.5 tok/s  (peak 58.0)**

## Time to first token

| Prompt | TTFT |
|---|---|
| short | 311ms |
| medium | 300ms |
| long | 412ms |

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
        "elapsed_s": 42.42093014717102,
        "tok_per_s": 12.069513757093901
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 11.531920194625854,
        "tok_per_s": 44.39850357606568
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 11.783894062042236,
        "tok_per_s": 43.44913466671701
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
        "elapsed_s": 10.51711893081665,
        "tok_per_s": 48.68253400651079
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 9.412078142166138,
        "tok_per_s": 54.39818839861077
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 8.82918119430542,
        "tok_per_s": 57.98952232741876
      }
    ]
  },
  {
    "prompt": "long",
    "runs": [
      {
        "prompt_tokens": 1066,
        "completion_tokens": 224,
        "total_tokens": 1290,
        "elapsed_s": 4.93296480178833,
        "tok_per_s": 45.40879754884813
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 10.246775150299072,
        "tok_per_s": 49.96694008505264
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 11.614887952804565,
        "tok_per_s": 44.08135507466268
      }
    ]
  }
]
```

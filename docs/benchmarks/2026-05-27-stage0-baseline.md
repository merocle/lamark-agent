# Lamark vLLM benchmark — stage0-baseline

- date: 2026-05-27 14:08:40
- endpoint: http://localhost:8000/v1
- model: qwen-base
- model root: /lamark/models/hf/Qwen_Qwen3.6-35B-A3B
- max_model_len: 131072

## Throughput (3 runs each, 512 tokens out)

| Prompt | Prompt tok | Compl tok | Wall avg | **tok/s avg** | tok/s peak |
|---|---|---|---|---|---|
| short | 750 | 512 | 17.43s | **29.4** | 30.3 |
| medium | 837 | 512 | 16.99s | **30.1** | 30.2 |
| long | 1066 | 512 | 17.10s | **29.9** | 30.0 |

**Overall avg: 29.8 tok/s  (peak 30.3)**

## Time to first token

| Prompt | TTFT |
|---|---|
| short | 429ms |
| medium | 420ms |
| long | 438ms |

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
        "elapsed_s": 18.305061101913452,
        "tok_per_s": 27.97040649847817
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 17.068047046661377,
        "tok_per_s": 29.99757374702987
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 16.903856992721558,
        "tok_per_s": 30.288945311147412
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
        "elapsed_s": 17.008943796157837,
        "tok_per_s": 30.10181032614477
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 16.997951984405518,
        "tok_per_s": 30.121275814270195
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 16.966190814971924,
        "tok_per_s": 30.177663659669694
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
        "elapsed_s": 17.1405189037323,
        "tok_per_s": 29.870740954552634
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 17.050514936447144,
        "tok_per_s": 30.028418608375862
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 17.112617015838623,
        "tok_per_s": 29.919444788959936
      }
    ]
  }
]
```

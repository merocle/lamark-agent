# Lamark vLLM benchmark — stage1-flags

- date: 2026-05-27 15:57:03
- endpoint: http://localhost:8000/v1
- model: qwen-base
- model root: /lamark/models/hf/Qwen_Qwen3.6-35B-A3B
- max_model_len: 131072

## Throughput (3 runs each, 512 tokens out)

| Prompt | Prompt tok | Compl tok | Wall avg | **tok/s avg** | tok/s peak |
|---|---|---|---|---|---|
| short | 750 | 512 | 22.97s | **24.9** | 30.2 |
| medium | 837 | 512 | 17.00s | **30.1** | 30.1 |
| long | 1066 | 512 | 17.05s | **30.0** | 30.5 |

**Overall avg: 28.3 tok/s  (peak 30.5)**

## Time to first token

| Prompt | TTFT |
|---|---|
| short | 386ms |
| medium | 378ms |
| long | 132ms |

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
        "elapsed_s": 34.6791512966156,
        "tok_per_s": 14.763913788454419
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 17.295353174209595,
        "tok_per_s": 29.603327254599336
      },
      {
        "prompt_tokens": 750,
        "completion_tokens": 512,
        "total_tokens": 1262,
        "elapsed_s": 16.934088945388794,
        "tok_per_s": 30.23487130905966
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
        "elapsed_s": 17.013500213623047,
        "tok_per_s": 30.093748703751828
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 16.98834490776062,
        "tok_per_s": 30.13830969290646
      },
      {
        "prompt_tokens": 837,
        "completion_tokens": 512,
        "total_tokens": 1349,
        "elapsed_s": 16.99193811416626,
        "tok_per_s": 30.13193648422855
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
        "elapsed_s": 17.445921182632446,
        "tok_per_s": 29.347834066205692
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 16.88297390937805,
        "tok_per_s": 30.32641066368037
      },
      {
        "prompt_tokens": 1066,
        "completion_tokens": 512,
        "total_tokens": 1578,
        "elapsed_s": 16.80793595314026,
        "tok_per_s": 30.461800986595385
      }
    ]
  }
]
```

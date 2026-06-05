# sisoul Examples

Working examples for common sisoul integration patterns. Each example is self-contained and runnable.

## Prerequisites

```bash
# 1. Daemon running
sisoul daemon --background

# 2. Verify
sisoul health
```

## Examples

| File | Purpose |
|---|---|
| `python_client_basic.py` | Connect to daemon, list cases, search, attest |
| `python_client_debate.py` | Multi-agent debate via HTTP |
| `python_client_pipeline.py` | End-to-end ask: case → LLM → attest → reputation |
| `python_skill_author.py` | Publish skill manifest to daemon |
| `bash_smoke_test.sh` | Bash one-liner smoke (curl all v2 endpoints) |
| `monitoring_grafana_query.txt` | Example Grafana queries against /sisoul/metrics |

## How to run

```bash
cd examples
python python_client_basic.py
bash bash_smoke_test.sh
```

## Contributing examples

Each example should:
- Be ≤ 100 LOC for readability
- Have clear comments
- Work with default daemon at `127.0.0.1:9876`
- Print expected output in the file header
- Add row to this README table

Pull requests welcome.

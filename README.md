# Lambda GPU Lock

Capacity watcher for Lambda Cloud. Polls the Lambda Cloud API for 1× 80GB GPU
availability (A100/H100), then automatically launches an instance with a
persistent filesystem and reports when it is live.

## Setup

```bash
cd ~/Desktop/lambda-gpu-lock
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in LAMBDA_API_KEY and LAMBDA_SSH_KEY_NAME
```

## Run

```bash
source .env
python watcher.py \
  --instance-type gpu_1x_h100_sxm5 \
  --region us-southeast-1 \
  --filesystem-name qwen38-models \
  --ssh-key-name "$LAMBDA_SSH_KEY_NAME" \
  --poll-interval 30 \
  --max-attempts 0   # 0 = run forever until capacity found
```

The script exits once the instance is `active`, printing the instance ID, IP,
region, and endpoint URL so you can SSH in and deploy.

## Requirements

- `LAMBDA_API_KEY` in environment (never printed or committed)
- `LAMBDA_SSH_KEY_NAME` — name of a registered Lambda SSH key
- Filesystem is created automatically in the target region if missing

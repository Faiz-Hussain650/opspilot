# Optional: deploy to Google Cloud Run with Terraform

> **This is the only part of OpsPilot that needs a credit card.** Google requires a billing
> account to use Cloud Run, even though this demo fits inside the monthly free tier
> (2M requests, 180k vCPU-seconds, 360k GiB-seconds). Everything else in the project is
> card-free. Skip this folder if you don't want to add a card.

What gets created:

- `opspilot-api` Cloud Run service (cloud mode: Groq + Qdrant Cloud), scales 0 -> 1
- `opspilot-ui` Cloud Run service pointing at the API
- Secret Manager secrets for the API keys (free tier: 6 active secret versions)
- A dedicated service account that can only read those secrets

## 0. Protect yourself first

1. Create a project at https://console.cloud.google.com and link billing.
2. **Billing -> Budgets & alerts -> Create budget** for **$1** with email alerts at 50%, 90%, 100%.
3. `max_instance_count = 1` and `min_instance_count = 0` in `main.tf` cap your exposure.

## 1. Tools

```bash
brew install --cask google-cloud-sdk
brew install terraform
gcloud auth login
gcloud auth application-default login
gcloud config set project <your-project-id>
```

## 2. Build amd64 images

Your Mac M3 builds **arm64** images by default; Cloud Run runs **amd64**. An arm64 image
fails on Cloud Run with `exec format error`. Use buildx to cross-build and push to Docker Hub
(free for public images; Cloud Run can pull from Docker Hub directly):

```bash
docker login
docker buildx build --platform linux/amd64 -f docker/api.Dockerfile \
  -t docker.io/<dockerhub-user>/opspilot-api:latest --push .
docker buildx build --platform linux/amd64 -f docker/ui.Dockerfile \
  -t docker.io/<dockerhub-user>/opspilot-ui:latest --push .
```

(The API build takes a while under emulation - 10 to 20 minutes is normal.)

## 3. Load the knowledge base into Qdrant Cloud

From the repo root, with your Qdrant Cloud URL/key and Groq key in `.env`:

```bash
make ingest-cloud
```

## 4. Deploy

```bash
cd deploy/gcp-cloudrun
cp terraform.tfvars.example terraform.tfvars   # fill it in - it's git-ignored
terraform init
terraform plan
terraform apply
```

Terraform prints `api_url` and `ui_url`. The first request after idle takes ~20-40 s
(cold start: the container boots and loads the ONNX models).

## 5. Tear down

```bash
terraform destroy
```

## Notes

- Secret values end up in the Terraform state file. That's fine for a local demo; in a team
  you'd use a remote state bucket with encryption, or create the secret versions outside Terraform.
- If you prefer Artifact Registry over Docker Hub, note its free tier is 0.5 GB of storage and the
  API image is larger than that, so it would cost a few cents a month.

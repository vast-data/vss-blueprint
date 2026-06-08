# Video Backend

REST API service for video search, authentication, and management.

## Table of Contents

- [User Authentication](#user-authentication)
- [Local NIM vs NVIDIA Cloud](#local-nim-vs-nvidia-cloud)
- [GUI Settings](#gui-settings)
- [Custom AI Prompts](#custom-ai-prompts)

---

## User Authentication

Users authenticate with their **VAST username + password** (same as VMS login).

### Setup

Configure backend secret (`deployments/vss-k8s-application/backend-secret.yaml`):

```yaml
vast_host: "<vms-ip-or-hostname>"
tenant_name: "default"
jwt_secret: "<openssl rand -hex 32>"
```

Users must have a VMS password set on their local account.

### How It Works

1. User enters username and password on the login page
2. Backend validates credentials against VMS `POST /api/token/{tenant}/` (falls back to `/api/token/`)
3. On success, backend issues a signed app JWT for the session

### Troubleshooting

- **Auth fails**: Verify user has a VMS password and `vast_host` / `tenant_name` match the cluster
- **Multi-tenant**: Set `tenant_name` to the correct tenant in `backend-secret.yaml`
- **Logs**: `kubectl logs -n <namespace> -l app=video-backend`

---

## Local NIM vs NVIDIA Cloud

| Setting | `true` (Local NIM) | `false` (NVIDIA Cloud) |
|---------|-------------------|------------------------|
| `embedding_local_nim` | No API key sent | Sends `nvidia_api_key` |
| `llm_local_nim` | No API key sent | Sends `nvidia_api_key` |

- **Local NIM**: Set flag to `true`, configure `host`/`port`/`scheme` for your endpoint
- **NVIDIA Cloud**: Set flag to `false`, use `integrate.api.nvidia.com:443`, set `nvidia_api_key`

---

## GUI Settings

### Advanced LLM Settings (Settings → Advanced LLM Settings)

| Setting | Description | Default |
|---------|-------------|---------|
| LLM Analysis Count | Results sent to LLM | 3 |
| Max Search Results | Max segments returned | 15 |
| Minimum Similarity | Vector similarity threshold | 0.1 |

### System Prompt (Settings → System Prompt)

Customize the LLM prompt for synthesizing search results. Stored in browser localStorage.

### Time Filtering

Filter search results by upload time:
- Presets: Last 5 min, 15 min, 1 hour, 24 hours, 1 week
- Custom: Select specific date range

---

## Custom AI Prompts

When uploading videos, check **"Use custom prompt"** to provide a custom AI reasoning prompt instead of predefined scenarios.

- Max 800 characters
- Overrides scenario selection
- Available in: Manual Upload, Streaming, Batch Sync dialogs

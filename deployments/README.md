# Deployment

Install a site from [vss-deploy](vss-deploy/README.md).

```bash
cd deployments/vss-deploy
cp site.yaml.example site.yaml
./deploy.sh site.yaml --check
./deploy.sh site.yaml
```

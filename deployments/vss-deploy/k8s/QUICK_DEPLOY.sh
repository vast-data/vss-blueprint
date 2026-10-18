#!/bin/bash
# Quick Deployment Script for Vast VSS Blueprint
# Usage: cd k8s && ./QUICK_DEPLOY.sh <namespace> <ingress_domain>

set -e

echo "🚀 Vast VSS Blueprint - Quick Deploy"
echo "======================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Validate required parameters
if [ -z "$1" ]; then
  echo -e "${RED}Error: Namespace parameter is required${NC}"
  echo ""
  echo "Usage: ./QUICK_DEPLOY.sh <namespace> <ingress_domain>"
  echo "Example: ./QUICK_DEPLOY.sh vastvideo vastdata.com"
  echo ""
  exit 1
fi

if [ -z "$2" ]; then
  echo -e "${RED}Error: Ingress domain parameter is required${NC}"
  echo ""
  echo "Usage: ./QUICK_DEPLOY.sh <namespace> <ingress_domain>"
  echo "Example: ./QUICK_DEPLOY.sh vastvideo vastdata.com"
  echo ""
  exit 1
fi

# Configuration
NAMESPACE="$1"
INGRESS_DOMAIN="$2"
cd "$(dirname "$0")"

# Check prerequisites
echo -e "${YELLOW}Checking prerequisites...${NC}"
command -v kubectl >/dev/null 2>&1 || { echo -e "${RED}kubectl not found${NC}"; exit 1; }

# Check kubectl configuration
echo -e "${YELLOW}Checking kubectl configuration...${NC}"
if ! kubectl cluster-info >/dev/null 2>&1; then
  echo -e "${RED}✗ kubectl is not configured or cannot connect to cluster${NC}"
  echo ""
  
  # Show current KUBECONFIG status
  if [ -n "$KUBECONFIG" ]; then
    echo -e "${YELLOW}Current KUBECONFIG: $KUBECONFIG${NC}"
    if [ ! -f "$KUBECONFIG" ]; then
      echo -e "${RED}  ⚠ File does not exist!${NC}"
    fi
  else
    echo -e "${YELLOW}KUBECONFIG environment variable is not set${NC}"
    DEFAULT_KUBECONFIG="$HOME/.kube/config"
    if [ -f "$DEFAULT_KUBECONFIG" ]; then
      echo -e "${YELLOW}  Found default config at: $DEFAULT_KUBECONFIG${NC}"
    else
      echo -e "${RED}  No default config found at: $DEFAULT_KUBECONFIG${NC}"
    fi
  fi
  echo ""
  
  echo -e "${YELLOW}Troubleshooting:${NC}"
  echo "  1. Check if KUBECONFIG environment variable is set:"
  echo "     echo \$KUBECONFIG"
  echo ""
  echo "  2. If not set, export your kubeconfig file:"
  echo "     export KUBECONFIG=/path/to/your/kubeconfig.yaml"
  echo ""
  echo "  4. Verify kubectl can connect:"
  echo "     kubectl cluster-info"
  echo ""
  echo -e "${RED}Please configure kubectl before running this script.${NC}"
  exit 1
fi
echo -e "${GREEN}✓ kubectl is configured and can connect to cluster${NC}"
echo -e "${GREEN}✓ All prerequisites found${NC}"
echo ""

echo -e "${YELLOW}Cluster Configuration:${NC}"
echo "  Ingress domain: $INGRESS_DOMAIN"
echo "  Namespace:    $NAMESPACE"
echo ""

# Step 1: Create Namespace
echo -e "${YELLOW}Step 1/6: Creating namespace...${NC}"
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -
kubectl label ns $NAMESPACE zarf.dev/agent=ignore
echo -e "${GREEN}✓ Namespace ready${NC}"
echo ""

# Step 2: Create Secret
echo -e "${YELLOW}Step 2/6: Creating backend secret...${NC}"
if [ ! -f backend-secret.yaml ]; then
  echo -e "${RED}Error: backend-secret.yaml not found next to this script${NC}"
  echo "deploy.sh writes it from site.yaml before this step."
  echo "For a manual run only:"
  echo "  cp backend-secret.yaml.example backend-secret.yaml"
  exit 1
fi
sed "s/NAMESPACE/$NAMESPACE/g" backend-secret.yaml | kubectl apply -f -
echo -e "${GREEN}✓ Secret created${NC}"
echo ""

# Step 3: Deploy Backend (includes backend ingress)
echo -e "${YELLOW}Step 3/6: Deploying backend...${NC}"
sed -e "s/NAMESPACE/$NAMESPACE/g" -e "s/INGRESS_DOMAIN/$INGRESS_DOMAIN/g" backend-deployment.yaml | kubectl apply -f -
echo -e "${GREEN}✓ Backend deployed${NC}"
echo ""

# Step 4: Deploy Frontend (includes frontend ingress)
echo -e "${YELLOW}Step 4/6: Deploying frontend...${NC}"
sed -e "s/NAMESPACE/$NAMESPACE/g" -e "s/INGRESS_DOMAIN/$INGRESS_DOMAIN/g" frontend-deployment.yaml | kubectl apply -f -
echo -e "${GREEN}✓ Frontend deployed${NC}"
echo ""

# Step 5: Deploy Video Streaming
echo -e "${YELLOW}Step 5/6: Deploying video streaming service...${NC}"
sed -e "s/NAMESPACE/$NAMESPACE/g" -e "s/INGRESS_DOMAIN/$INGRESS_DOMAIN/g" videostreamer-deployment.yaml | kubectl apply -f -
echo -e "${GREEN}✓ Video streaming service deployed${NC}"
echo ""

# Step 6: Deploy Video Batch Sync
echo -e "${YELLOW}Step 6/6: Deploying video batch sync service...${NC}"
sed -e "s/NAMESPACE/$NAMESPACE/g" -e "s/INGRESS_DOMAIN/$INGRESS_DOMAIN/g" video-batch-sync-deployment.yaml | kubectl apply -f -
echo -e "${GREEN}✓ Video batch sync service deployed${NC}"
echo ""

# Get Ingress IP
echo -e "${GREEN}✅ Deployment Complete!${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Deployment Status:"
kubectl get all -n $NAMESPACE
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "🌐 Access Information:"
echo ""
INGRESS_IP=$(kubectl get ingress video-frontend-ingress -n $NAMESPACE -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")
if [ "$INGRESS_IP" == "pending" ] || [ -z "$INGRESS_IP" ]; then
  echo -e "${YELLOW}⏳ Ingress IP is still pending...${NC}"
  echo "   Run this to check: kubectl get ingress -n $NAMESPACE"
else
  echo -e "${GREEN}Ingress IP: $INGRESS_IP${NC}"
fi
echo ""
echo "Add to /etc/hosts:"
echo -e "${YELLOW}  $INGRESS_IP vss-lab.$INGRESS_DOMAIN vss-backend.$INGRESS_DOMAIN${NC}"
echo ""
echo "Then access:"
echo -e "${GREEN}  http://vss-lab.$INGRESS_DOMAIN${NC} (Main Video Lab)"
echo -e "${GREEN}  http://vss-backend.$INGRESS_DOMAIN/api${NC} (Backend API)"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📝 Next Steps:"
echo "  1. Wait for pods to be ready:"
echo "     kubectl get pods -n $NAMESPACE -w"
echo "  2. Add Ingress IP to your local machine's /etc/hosts"
echo "  3. Open http://vss-lab.$INGRESS_DOMAIN"
echo "  4. Login with VAST credentials"
echo ""
echo "🔍 View Logs:"
echo "  Backend:         kubectl logs -f -n $NAMESPACE -l app=video-backend"
echo "  Frontend:        kubectl logs -f -n $NAMESPACE -l app=video-frontend"
echo "  Video Streaming: kubectl logs -f -n $NAMESPACE -l app=video-stream-capture"
echo "  Batch Sync:      kubectl logs -f -n $NAMESPACE -l app=video-batch-sync"
echo ""


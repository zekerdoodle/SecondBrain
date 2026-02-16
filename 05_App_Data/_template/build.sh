#!/bin/bash
# Build app into single index.html
cd "$(dirname "$0")"
npm run build
echo "Built to index.html"

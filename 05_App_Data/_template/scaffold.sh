#!/bin/bash
# Usage: ./scaffold.sh my-new-app "My New App" "Description here"
#
# Creates a new app from this template in ../my-new-app/
# Then run: cd ../my-new-app && npm run dev

set -e

APP_NAME=$1
DISPLAY_NAME=${2:-$APP_NAME}
DESCRIPTION=${3:-"A Second Brain app"}

if [ -z "$APP_NAME" ]; then
  echo "Usage: ./scaffold.sh <app-name> [\"Display Name\"] [\"Description\"]"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET="$SCRIPT_DIR/../$APP_NAME"

if [ -d "$TARGET" ]; then
  echo "Error: $TARGET already exists"
  exit 1
fi

cp -r "$SCRIPT_DIR" "$TARGET"
rm -rf "$TARGET/node_modules" "$TARGET/.git"

cd "$TARGET"

# Update package.json name
sed -i "s/brain-app-template/$APP_NAME/g" package.json

# Update store namespace in main.js
sed -i "s/const APP_NAME = 'brain-app-template'/const APP_NAME = '$APP_NAME'/g" src/main.js

# Update title in index.html
sed -i "s/<title>Hello Brain<\/title>/<title>$DISPLAY_NAME<\/title>/g" src/index.html

npm install

echo "Scaffolded $APP_NAME at $TARGET"
echo "Next: cd $TARGET && npm run dev"

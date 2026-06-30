# Node 20 LTS slim image — smaller than the default Debian variant.
FROM node:20-slim

WORKDIR /app

# Install dependencies before copying source so Docker layer-caching avoids
# re-running npm install on every source change.
COPY package.json package-lock.json* ./
RUN npm install

# Copy source AFTER npm install so edits to src/ don't invalidate the
# node_modules layer.  The compose file mounts ./frontend/src over /app/src
# for live HMR anyway, so this COPY is mainly for production builds.
COPY . .

EXPOSE 3000

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

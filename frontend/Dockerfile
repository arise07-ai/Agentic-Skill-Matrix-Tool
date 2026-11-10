FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# `npm ci` when a lock file exists (exact, repeatable versions); otherwise `npm install`.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi
COPY . .
# The /api proxy target is baked into the build (see next.config.mjs). On Railway set the service variable
#   BACKEND_URL=http://backend.railway.internal:8000
# (the backend service must be named "backend" and have PORT=8000). Changing it needs a rebuild.
ARG BACKEND_URL=http://backend:8000
ENV BACKEND_URL=$BACKEND_URL
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
EXPOSE 3000
# Railway sets PORT at run time and this overrides the default below.
ENV PORT=3000 HOSTNAME=0.0.0.0
CMD ["node", "server.js"]

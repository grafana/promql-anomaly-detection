# Copyright The OpenTelemetry Authors
# SPDX-License-Identifier: Apache-2.0


FROM node:20-alpine@sha256:643e7036aa985317ebfee460005e322aa550c6b6883000d01daefb58689a58e2 AS deps
RUN apk add --no-cache libc6-compat

WORKDIR /app

COPY ./src/frontend/package*.json ./
RUN npm ci

FROM node:20-alpine@sha256:643e7036aa985317ebfee460005e322aa550c6b6883000d01daefb58689a58e2 AS builder
RUN apk add --no-cache libc6-compat protobuf-dev protoc
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY ./pb ./pb
COPY ./src/frontend .

RUN npm run grpc:generate
RUN npm run build

FROM node:20-alpine@sha256:643e7036aa985317ebfee460005e322aa550c6b6883000d01daefb58689a58e2 AS runner
WORKDIR /app
RUN apk add --no-cache protobuf-dev protoc

ENV NODE_ENV=production

RUN addgroup -g 1001 -S nodejs
RUN adduser -S nextjs -u 1001

COPY --from=builder /app/next.config.js ./
COPY --from=builder /app/utils/telemetry/Instrumentation.js ./
COPY --from=builder /app/public ./public
COPY --from=builder /app/package.json ./package.json
COPY --from=deps /app/node_modules ./node_modules

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

ENV PORT 8080
EXPOSE ${PORT}

ENTRYPOINT npm start

FROM node:24.19.0-bookworm-slim AS build
WORKDIR /app
RUN npm install --global pnpm@11.25.0
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:24.19.0-bookworm-slim
WORKDIR /app
COPY --from=build /app/dist ./dist
COPY server.mjs .
USER node
EXPOSE 8080
CMD ["node", "server.mjs"]

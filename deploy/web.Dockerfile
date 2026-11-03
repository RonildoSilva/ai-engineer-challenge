# Interface web: build do Vite (Node fixado) servido por nginx (fixado). Imagens por digest.
#   docker build -f deploy/web.Dockerfile web
FROM node:20.20.0-alpine@sha256:09e2b3d9726018aecf269bd35325f46bf75046a643a66d28360ec71132750ec8 AS build
WORKDIR /web
COPY package.json package-lock.json .npmrc ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.29.3-alpine@sha256:b3c656d55d7ad751196f21b7fd2e8d4da9cb430e32f646adcf92441b72f82b14
COPY --from=build /web/dist /usr/share/nginx/html

FROM node:22-bookworm

# Build-time install: avoid runtime npm permission/network instability
RUN npm install -g openclaw@latest

WORKDIR /home/node

# Keep data under /home/node/.openclaw (mounted from host)
ENV HOME=/home/node


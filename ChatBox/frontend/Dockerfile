# Use a Node image for the Vite development server.
FROM node:20-slim

WORKDIR /app

# Copy the manifest first so dependency installation can be cached.
COPY package.json ./

# A package-lock.json will be added when dependencies are installed locally.
# npm install keeps this initial scaffold runnable before that lockfile exists.
RUN npm install

COPY . .

EXPOSE 3000

# Vite must listen on all interfaces so the host browser can reach it through
# the Docker port mapping.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "3000"]

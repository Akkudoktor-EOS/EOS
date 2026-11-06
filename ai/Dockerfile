# ai/Dockerfile
# AI development sandbox for AkkudoktorEOS.
# Runs as a non-root user whose UID/GID match the host to keep bind-mount ownership sane.
FROM debian:bookworm-slim

ARG UID=1000
ARG GID=1000
ARG USERNAME=dev

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/home/${USERNAME}/.local/bin:${PATH}"

# Base tooling: git, GitHub CLI, Python 3.13, and Node.js (required by Claude Code / OpenCode).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gh \
    gnupg \
    sudo \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy uv binary from the official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install python 3.13
RUN uv python install 3.13

# Install Node.js 22.x (needed for the AI CLIs).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI (global npm package).
RUN npm install -g @anthropic-ai/claude-code

# OpenCode CLI (global npm package).
RUN npm install -g opencode-ai

# Qwen Code CLI
RUN curl -fsSL https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.sh | bash -

# Create a non-root user matching the host UID/GID.
RUN groupadd -g ${GID} ${USERNAME} 2>/dev/null || true \
    && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

# Install the entrypoint into the read-only image layer, owned by root.
# It must NOT live under /home/dev: that path is the persistent `ai-home`
# volume (mounted with nocopy), so a copy there would be shadowed by a stale
# file from the volume and would be writable by the agent.
COPY --chown=root:root --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER ${USERNAME}
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]

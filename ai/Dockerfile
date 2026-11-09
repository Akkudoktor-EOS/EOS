# ai/Dockerfile
# AI development sandbox for AkkudoktorEOS.
# Runs as a non-root user whose UID/GID match the host to keep bind-mount ownership sane.

# uv comes from its official image. BuildKit does not expand variables in
# `COPY --from`, so the image is a named stage with a global ARG. Keep
# UV_VERSION in sync with .github/workflows/pre-commit.yml.
ARG UV_VERSION=0.12.9
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM debian:bookworm-slim

ARG UID=1000
ARG GID=1000
ARG USERNAME=dev

# Pinned tool versions (UV_VERSION is above). Bump them and rebuild
# (AI_SANDBOX_REBUILD=1) to update.
ARG CLAUDE_CODE_VERSION=2.1.319
ARG OPENCODE_VERSION=1.19.5
ARG QWEN_CODE_VERSION=0.27.0

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/home/${USERNAME}/.local/bin:${PATH}"

# Base tooling: git, GitHub CLI and build tools. No sudo: the agent must not
# be able to become root, even if no-new-privileges were removed.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gh \
    gnupg \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy the uv binary from the uv stage.
COPY --from=uv /uv /uvx /bin/

# Install Python 3.13 outside /root (mode 0700, unreachable for the dev user)
# and link it into /usr/local/bin, where uv finds it as an interpreter on PATH.
RUN UV_PYTHON_INSTALL_DIR=/opt/uv-python UV_PYTHON_BIN_DIR=/usr/local/bin \
        uv python install 3.13 \
    && chmod -R a+rX /opt/uv-python \
    && /usr/local/bin/python3.13 --version

# Install Node.js 22.x (needed for the AI CLIs).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# AI CLIs as global npm packages (installed under /usr, readable for everyone).
RUN npm install -g \
    "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
    "opencode-ai@${OPENCODE_VERSION}" \
    "@qwen-code/qwen-code@${QWEN_CODE_VERSION}" \
    && npm cache clean --force

# Create a non-root user matching the host UID/GID.
RUN groupadd -g ${GID} ${USERNAME} 2>/dev/null || true \
    && useradd -m -u ${UID} -g ${GID} -s /bin/bash ${USERNAME}

# Install the entrypoint into the read-only image layer, owned by root.
# It must NOT live under /home/dev: that path is the persistent `ai-home`
# volume, so a copy there would be shadowed by a stale file from the volume
# and would be writable by the agent.
COPY --chown=root:root --chmod=0755 entrypoint.sh /usr/local/bin/entrypoint.sh

USER ${USERNAME}
WORKDIR /workspace

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["bash"]

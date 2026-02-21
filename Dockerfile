FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Core tools (tmux for multi-agent visibility)
RUN apt-get update && apt-get install -y \
    git tmux curl nodejs npm python3 python3-pip jq \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python deps for clambake
RUN pip3 install --break-system-packages psycopg2-binary

# Create non-root user (Claude blocks --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash agent && \
    mkdir -p /home/agent/.claude && \
    mkdir -p /tmp/clambake-tmux && chmod 1777 /tmp/clambake-tmux

# Pre-configure Claude Code to skip onboarding wizard
RUN echo '{"hasCompletedOnboarding":true,"theme":"dark","customApiKeyResponses":{"approved":["ANTHROPIC_API_KEY"]}}' \
    > /home/agent/.claude.json && \
    chown agent:agent /home/agent/.claude.json /home/agent/.claude

# tmux config: shared socket dir so all agents can see panes
ENV TMUX_TMPDIR=/tmp/clambake-tmux

# Copy clambake CLI into container
COPY clambake.py schema.sql agent-worker.sh launch-tmux.sh /opt/clambake/
RUN chmod +x /opt/clambake/*.sh && \
    ln -s /opt/clambake/clambake.py /usr/local/bin/clambake && \
    chmod +x /opt/clambake/clambake.py

# Workspace mount point
VOLUME /workspace
WORKDIR /workspace

# Switch to non-root user
USER agent

ENTRYPOINT ["bash"]

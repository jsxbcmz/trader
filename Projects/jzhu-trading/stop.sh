#!/bin/bash
echo "Stopping 小宇量化 (JZhu Trading)..."
# compose down stops containers and releases their host-port bindings.
# (Deliberately no lsof/kill on 3000/8180: on Docker Desktop those ports are
# held by com.docker.backend / vpnkit, and killing that pid crashes Docker.)
docker compose down 2>/dev/null
echo "Stopped."

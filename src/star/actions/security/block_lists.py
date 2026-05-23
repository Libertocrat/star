"""Binary blocklists for STAR action runtime security."""

# Shells -> direct arbitrary command execution (RCE primitives)
BLOCKED_SHELLS: tuple[str, ...] = (
    "ash",
    "bash",
    "dash",
    "sh",
    "zsh",
)

# Interpreters -> full standalone RCE capability (code execution, sockets, file I/O)
BLOCKED_INTERPRETERS: tuple[str, ...] = (
    "python",
    "python3",
    "py",
    "perl",
    "ruby",
    "node",
)

# Network / remote interaction -> reverse shells, data exfiltration, remote control
BLOCKED_NETWORK: tuple[str, ...] = (
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "socat",
    "ssh",
    "scp",
    "ftp",
    "telnet",
)

# Filesystem mutation -> direct file system modification
BLOCKED_FILESYSTEM: tuple[str, ...] = (
    "rm",
    "mv",
    "cp",
    "ln",
    "unlink",
    "dd",
)

# Permissions / ownership -> can break isolation and security boundaries
BLOCKED_PERMISSIONS: tuple[str, ...] = (
    "chmod",
    "chown",
    "chgrp",
)

# User / privilege management -> persistence and privilege escalation
BLOCKED_PRIVILEGE: tuple[str, ...] = (
    "sudo",
    "su",
    "useradd",
    "userdel",
    "usermod",
    "groupadd",
    "groupdel",
    "groupmod",
    "passwd",
    "chage",
)

# Infrastructure / container control -> container escape or external system control
BLOCKED_INFRA: tuple[str, ...] = (
    "docker",
    "docker-compose",
    "compose",
    "kubectl",
)

# Low-level filesystem / disk -> can break sandbox or corrupt system state
BLOCKED_FILESYSTEM_LOW_LEVEL: tuple[str, ...] = (
    "mount",
    "umount",
    "mkfs",
    "mkfs.ext4",
    "fsck",
    "fsck.ext4",
)

# Process control -> can disrupt or terminate system processes
BLOCKED_PROCESS: tuple[str, ...] = (
    "killall",
    "pkill",
)

# Debug / tracing -> runtime introspection and sensitive data exposure
BLOCKED_DEBUG: tuple[str, ...] = (
    "strace",
    "ltrace",
)

# Network introspection -> internal visibility and recon (optional strict mode)
BLOCKED_NETWORK_INTROSPECTION: tuple[str, ...] = (
    "tcpdump",
    "ss",
    "netstat",
)

# Environment/execution context manipulation to
# process behavior control and persistence
BLOCKED_ENV: tuple[str, ...] = (
    "env",
    "nohup",
)

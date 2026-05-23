# Security Policy

Secure Templated Actions Runtime (STAR) is a secure automation runtime for workflows and AI agents that need predefined system-level actions without arbitrary shell execution. The project includes authentication, a DSL-defined action registry, request validation middleware, managed file storage under `/v1/files`, authenticated action discovery and execution under `/v1/actions`, and container-based isolation.

In STAR, an action is a predefined command template compiled from validated YAML specs. Clients can execute only the actions present in the runtime registry; they cannot submit arbitrary shell commands.

The authenticated application API is centered on `GET /v1/actions`, `GET /v1/actions/{action_id}`, `POST /v1/actions/{action_id}`, and the protected `/v1/files` routes.

Detailed security design and threat analysis are documented separately:

- [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md): system architecture and security-relevant components
- [docs/THREAT_MODEL.md](./docs/THREAT_MODEL.md): threat analysis, trust boundaries, and mitigations
- [docs/CI.md](./docs/CI.md): automated testing, security checks, release, and OpenAPI docs workflows

This document focuses on vulnerability reporting and coordinated disclosure.

## Deployment Model

STAR is intended to run as an internal microservice inside trusted container infrastructure. It is typically deployed on a Docker network for service-to-service access and may also be published to localhost for trusted host-local access.

The service is not designed to be exposed directly to the public Internet.

> [!WARNING]
> Do not expose STAR directly on a public edge. The service assumes a trusted deployment boundary and intentionally leaves `/health`, `/metrics`, and OpenAPI docs endpoints unauthenticated while docs remain enabled.

## Supported Versions

Only the `main` branch is currently supported with security updates.

Pre-release versions, development branches, and forks are not guaranteed to receive security fixes.

## Reporting a Vulnerability

If you discover a security vulnerability or have security concerns, please report them directly to the project maintainer.

Contact:

Libertocrat - <libertocrat@proton.me>

Please include the following information:

- a clear summary of the issue
- affected versions such as commit SHA, tag, or release version
- environment details such as OS, Python version, and container runtime
- reproducible steps or a minimal proof of concept
- relevant logs or configuration details, sanitized of secrets
- potential impact such as data exposure, privilege escalation, or denial of service
- whether the issue affects the DSL build pipeline, runtime execution path, or `/v1/files` storage model

If the issue allows escalation or access to sensitive data, include **SECURITY** in the email subject to prioritize the report.

Please do not publish details about the vulnerability publicly until a fix or mitigation plan has been provided.

## Response and Handling

Security reports will be handled confidentially.

The maintainer aims to:

- Acknowledge critical reports within **72 hours**
- Provide an estimated remediation timeline after initial triage
- Coordinate disclosure with the reporter

## Preferred Disclosure Channels

- **GitHub Security Advisories** (if it's available): allows private disclosure, coordinated disclosure, and optionally requesting CVE assignment.
- **Email to the project contact** (Libertocrat - <libertocrat@proton.me>): acceptable for private reports when Security Advisories are not available.

> [!IMPORTANT]
> Use a private disclosure channel for vulnerabilities. Public issues are not an appropriate reporting path for security-sensitive findings.

## Reporter Checklist

When reporting, include as much of the following as possible:

- A short, clear summary of the issue
- Affected versions and environment details
- Reproducible steps or a minimal proof of concept
- Relevant logs, stack traces, or sanitized configuration files
- Impact assessment such as data exposure, RCE, privilege escalation, or denial of service
- Whether encrypted communication is required

## Secure Attachments (Optional)

If you need to send sensitive files, screenshots, or proofs of concept (PoCs), you may encrypt them using the maintainer's public PGP key.

The public PGP key is available in the file [SECURITY_PGP_KEY.asc](SECURITY_PGP_KEY.asc) at the root of this repository.

> **PGP fingerprint**:
> 0093 2D8B E725 68F8 7C60  D138 B00F 1868 1AFD 0A6F

### Verify the PGP key (optional)

```bash
gpg --show-keys --fingerprint SECURITY_PGP_KEY.asc
```

## Coordination and Disclosure

Vulnerabilities will be disclosed publicly only after:

- A fix or mitigation has been implemented
- Disclosure has been coordinated with the reporter

The project follows a responsible disclosure approach in order to protect users while security fixes are prepared.

---

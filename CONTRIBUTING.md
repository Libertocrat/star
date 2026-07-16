# Contributing

## 1. Introduction

Thank you for your interest in contributing to the Secure Templated Actions Runtime (STAR).

STAR is an open source secure automation runtime focused on predefined actions, authenticated API-based execution, managed file operations, and container-oriented isolation for workflow and AI-agent environments.

## 2. Contribution Status

The repository is not currently accepting external code contributions.

> [!IMPORTANT]
> External pull requests are currently paused while the project stabilizes its public API, security model, DSL action surface, and release workflow.

The project is still stabilizing several core areas before opening the pull request process to external contributors:

- API design
- security model
- module and action surface
- testing coverage
- CI workflows
- release process

## 3. Future Contribution Model

Once external contributions are enabled, the repository will publish explicit guidelines for:

- branching strategy
- pull request workflow
- code style requirements
- testing expectations
- DSL spec review expectations
- security review requirements
- contribution licensing terms

Those rules are not defined yet and should not be assumed before they are documented.

## 4. License Expectations

STAR is licensed under the GNU Affero General Public License v3.0. See [LICENSE](LICENSE) for the full license text.

The project may offer separate commercial licenses for proprietary use cases that cannot comply with the AGPLv3. Because external code contributions can affect future dual-licensing options, external pull requests remain paused until the project publishes explicit contribution licensing terms.

## 5. Providing Feedback

Feedback is still welcome while the project is stabilizing.

Useful feedback includes:

- architecture suggestions
- DSL ergonomics observations
- documentation improvements
- bug reports
- usability observations around `/v1/actions`, `/v1/files`, and generated OpenAPI docs

For non-security topics, use the GitHub issue tracker.

## 6. Security Reporting

Security vulnerabilities must be reported privately.

Do not disclose security issues through public GitHub issues.

Follow the responsible disclosure process defined in [SECURITY.md](SECURITY.md).

## 7. Development Documentation

Developers who want to work with the codebase locally should use [DEVELOPMENT.md](DEVELOPMENT.md).

The development guide covers:

- local environment setup
- authenticated action and file API routes under `/v1/actions` and `/v1/files`
- DSL spec development and validation flow
- Makefile workflow
- CI reproduction
- pre-commit hooks
- helper scripts in `scripts/`
- troubleshooting

## 8. Related Documentation

The main technical and project documents are:

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)
- [docs/TESTING.md](docs/TESTING.md)
- [docs/CI.md](docs/CI.md)
- [SECURITY.md](SECURITY.md)
- [DEVELOPMENT.md](DEVELOPMENT.md)
- [scripts/README.md](scripts/README.md)

These documents describe the internal design, DSL execution model, file API, testing strategy, release workflows, local development process, and helper scripts for STAR.

---

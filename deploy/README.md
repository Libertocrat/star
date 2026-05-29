# STAR Runtime Package

Safe actions. No raw shell.

This package contains the deployable STAR runtime control surface.

## What is included

- `./star` - the top-level lifecycle command for configure, startup, demos, status, logs, and shutdown
- `./star-runtime/` - Docker Compose runtime assets, runtime scripts, secrets directory, and local configuration files

> [!IMPORTANT]
> Most users should manage STAR from the directory that contains `./star`.
>
> You usually do not need to enter `star-runtime/` unless you want to adjust `.env`, inspect `secrets/star_api_token.txt`, or add custom YAML specs under `user-specs/`.

In the default deploy flow, the generated STAR configuration enables Swagger / OpenAPI docs for local testing and demos.

`--production` changes those defaults when STAR generates a new configuration, but an existing `star-runtime/.env` remains authoritative until you overwrite it or edit `STAR_ENABLE_DOCS` manually.

## Start

Run the guided flow:

```bash
./star
```

This checks whether STAR is configured, offers to run the configuration wizard, starts the runtime, and points you to the next commands.

## Fast deploy

Run the non-interactive default flow:

```bash
./star --auto
```

Run configuration, startup, and a guided demo in one sequence:

```bash
./star --auto --demo
```

> [!NOTE]
> `'./star --silent'` is also available for minimal-output automation, and `'./star --production'` applies production-oriented configure/start behavior.

Built-in demos use `curl` and `jq`. If they are missing, the demo flow can prompt to install them automatically when possible.

## Useful commands

```bash
./star status
./star demo
./star demo --demo encrypt --auto
./star logs -f
./star down
```

Useful explicit subcommands:

```bash
./star configure --auto
./star up --pull
./star logs --tail 200
./star down --docker-cleanup --force
```

## Swagger / OpenAPI docs

Swagger / OpenAPI docs are enabled by default in the standard local deploy flow so STAR is easier to explore and test.

> [!WARNING]
> For production-oriented deployments, prefer `'./star configure --force --production'` when you want to regenerate configuration with production-oriented defaults, or set `STAR_ENABLE_DOCS=false` manually in `.env`.

If you use `--production`, configure with production-oriented settings, or manually disable docs in `.env`, you can re-check STAR status as follows:

1. Safely stop STAR with `'./star down'`
2. Restart with `'./star up'`
3. Run `'./star status'` to verify config and runtime

## Customize

You may want to edit or inspect:

- `star-runtime/.env`
- `star-runtime/secrets/star_api_token.txt`
- `star-runtime/user-specs/`

> [!WARNING]
> Keep the API token secret. Do not commit real secrets or environment files to version control.

## Full docs

For the full project overview and deeper documentation, use the public repository:

- [STAR repository](https://github.com/Libertocrat/star)
- [Main README](https://github.com/Libertocrat/star/blob/main/README.md)
- [Development guide](https://github.com/Libertocrat/star/blob/main/DEVELOPMENT.md)
- [Architecture guide](https://github.com/Libertocrat/star/blob/main/docs/ARCHITECTURE.md)
- [Threat model](https://github.com/Libertocrat/star/blob/main/docs/THREAT_MODEL.md)
- [Security policy](https://github.com/Libertocrat/star/blob/main/SECURITY.md)
- [Hosted OpenAPI docs (not interactive)](https://libertocrat.github.io/star/api-docs/)

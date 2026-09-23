# Chameleon project instructions

This repository backs a live Home Assistant installation. Preserve the existing `chameleon` config entry, entity unique IDs, entity IDs, configured light order, scenes, and automation consumers during upgrades.

Before a live change, inspect the current integration, entities, HACS source, and all configuration references. Follow the Home Assistant MCP best-practice skill and its relevant references. Prefer reversible changes; take a Home Assistant backup when available and record any backup limitation. Do not edit Home Assistant `.storage` files directly.

For every code or live Home Assistant change, add a dated entry to `docs.md` describing the request, affected objects, actions, validation, remaining limits, and rollback path. Test the integration after deployment using the MCP connection and read back the affected entities and consumers. Keep credentials, webhook URLs, private household data, and backups out of Git.

When asked to publish, review the diff for secrets, commit, and push to the configured private GitHub remote. Report any synchronization failure rather than claiming the remote was updated.

# crates-policies

Declarative crates.io policies for Astral crates.

Trusted publishing policies live in `trusted-publishing/*.json`. Each policy declares the
GitHub repository, workflow, environment, whether to require trusted publishing for new
versions, and the crates that use that configuration. A crate may appear in only one
policy.

Run a read-only dry run with a crates.io token that has the `trusted-publishing` scope:

```console
CARGO_REGISTRY_TOKEN=... ./apply.py
```

Apply the policies after reviewing the output:

```console
CARGO_REGISTRY_TOKEN=... ./apply.py --confirm
```

The utility checks every crate before making changes. It removes stale or duplicate
GitHub trusted-publisher configurations, adds the declared configuration when missing,
and reconciles `trustpub_only`. It does not publish new crates; an initial publish must
happen before trusted publishing can be configured.

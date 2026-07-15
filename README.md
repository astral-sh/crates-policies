# crates-policies

Declarative crates.io policies for Astral crates.

Trusted publishing policies live in `trusted-publishing/*.json`. Each policy declares the
GitHub repository, workflow, environment, whether to require trusted publishing for new
versions, and the crates that use that configuration. A crate may appear in only one
policy.

The Ty component crates in `trusted-publishing/ty.json` currently publish from the
Ruff release workflow, so their policy intentionally references `astral-sh/ruff`.

Check that every publishable crate in a Cargo workspace is configured:

```console
./check.py /path/to/workspace
```

The checker infers the GitHub repository from `workspace.package.repository` and
reports any new crates that need to be bootstrapped. Use `--repository OWNER/NAME` to
override the inferred repository.

Run a read-only dry run with a crates.io token that has the `trusted-publishing` scope:

```console
CARGO_REGISTRY_TOKEN=... ./apply.py
```

Apply the policies after reviewing the output:

```console
CARGO_REGISTRY_TOKEN=... ./apply.py --confirm
```

The `Apply crates.io policies` workflow can also be run manually from GitHub Actions.
It reads `CARGO_REGISTRY_TOKEN` from the `production` environment and performs a dry
run unless `confirm` is selected.

The utility checks every crate before making changes. It removes stale or duplicate
GitHub trusted-publisher configurations, adds the declared configuration when missing,
and reconciles `trustpub_only`. It does not publish new crates; an initial publish must
happen before trusted publishing can be configured.

## License

Licensed under either [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT) at your option.

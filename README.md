# crates-policies

Declarative crates.io policies for Astral crates.

Trusted publishing policies live in `trusted-publishing/*.json`. Each policy declares the
GitHub repository, workflow, environment, whether to require trusted publishing for new
versions, and the crates that use that configuration. A crate may appear in only one
policy.

Check that every publishable crate in a Cargo workspace is configured:

```console
./check.py /path/to/workspace
```

The checker infers the GitHub repository from `workspace.package.repository` or
`package.repository` and reports any new crates that need to be bootstrapped. Use
`--repository OWNER/NAME` to override the inferred repository.

To register new crates:

1. Add the crate names to the appropriate `trusted-publishing/*.json` policy in sorted order.
2. Run the [`Apply` workflow](https://github.com/astral-sh/crates-policies/actions/workflows/apply.yml)
   with `confirm` enabled to bootstrap the crates.
3. Re-run the workspace check or release preparation.

The `Apply` workflow reads `CARGO_REGISTRY_TOKEN` from the `production` environment,
which requires the `publish-new` and `trusted-publishing` scopes, and performs a dry run
unless `confirm` is selected.

The utility checks every crate before making changes. It removes stale or duplicate
GitHub trusted-publisher configurations, adds the declared configuration when missing,
and reconciles `trustpub_only`. For a new crate, it publishes a placeholder release
before configuring trusted publishing.

## License

Licensed under either [Apache License, Version 2.0](LICENSE-APACHE) or
[MIT license](LICENSE-MIT) at your option.

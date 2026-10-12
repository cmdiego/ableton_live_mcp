# Contributing

Issues, bug reports and ideas are welcome. Please open an issue before a large pull request so we can
agree on the approach first. New tools have a 40-tool budget and a planning protocol
([docs/api-protocol.md](docs/api-protocol.md)).

## How a pull request lands

This repository only receives tested releases; development happens in a private repository
([D32](docs/decisions.md)). Open pull requests here as usual. Once one is accepted, its commits (with
you as the author) move into development, and the pull request is closed with a note naming the
release that will include it. It shows as "closed" rather than "merged", but your work ships in that
release and the [changelog](CHANGELOG.md) credits it.

## License and CLA

The project is licensed under the [Apache License 2.0](LICENSE). Before a pull request can be accepted,
its author signs the [Contributor License Agreement](CLA.md) once: the CLA bot comments on your first
pull request with the sentence to post. You keep the copyright in your work. The CLA lets the
maintainer also offer the project under other terms in the future, including commercial ones
([D31](docs/decisions.md)).

Only submit work you wrote yourself, or that is under a license compatible with Apache 2.0 (say which
in the pull request). Don't copy code from projects under GPL or other copyleft licenses.

## Development

Setup, tests and the rules for the Remote Script are in [AGENTS.md](AGENTS.md) (they apply to people
too) and the [README](README.md).

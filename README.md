# PUB-TOOL

PUB-TOOL is a Windows desktop workspace for biomedical literature searches:
MeSH/PubChem alias lookup, editable PubMed query graphs, title/abstract retrieval,
manual screening, Excel/EndNote RIS export, and a local-model connection interface.

## Install and launch

Download a package from [Releases](https://github.com/GammaHH/PUB-TOOL-update/releases).
Install or extract it and open the PUB-TOOL launcher. It uses the same PyAppify
launcher as OK applications, with install status, version selection, Auto Start,
stable/prerelease update modes and an application Start button.

The first installation may download an isolated Python 3.12 runtime and Python
dependencies. Node.js is not required on an end user's machine. Keep the launcher
open while installation/update is in progress. Launching the APP and managing
updates are separate operations.

## Updates

PyAppify checks Git tags in this repository. Stable versions use tags such as
`v0.3.0`; preview versions use semantic-version prerelease tags. The launcher's
stable automatic update mode excludes previews. Version rollback uses the
launcher's version menu; schema compatibility must be checked before downgrading.

This repository contains only update runtime content and release packaging
metadata. Full development history, tests, tools, local databases, logs and
credentials are not published here. Runtime Python and bundled JavaScript files
are necessarily visible in this source-based update model.

Debug Python support modules are intentionally included in the runtime payload.
Normal Launcher starts keep developer mode off; source-write/local-Git actions
also require a separate development checkout marker, which is not distributed.
The source Debug entry, Vite server, frontend sources, tests and packaging tools
are not part of an application update. Launcher EXE customizations need a local
Launcher rebuild. Model weights/LM Studio are managed separately.

The release manifest records payload SHA-256 hashes and distribution boundaries.
See [CHANGELOG](CHANGELOG.md) for the changes in each runtime release.

## User data

Windows default: `%LOCALAPPDATA%\PUB-TOOL\literature.sqlite3`.
It is separate from the launcher's update checkout. Close all APP windows before
backing up the database. An update is not a database backup. Appending newer
schema tables does not imply compatibility with arbitrary older application tags.

Queries, aliases and screening decisions remain user-controlled. PubMed abstracts
are not full article text. A record without an abstract is distinct from a failed
download. OneNote currently receives rich-text clipboard output; native .one
export and account synchronization are not included.

## Release verification

The runtime manifest lists SHA-256 hashes of the staged update payload. This is
a consistency check, not a digital signature. Launcher downloads and a real
first-install/update flow still need validation on Windows after packaging.

The launcher is built with [PyAppify](https://github.com/ok-oldking/pyappify).

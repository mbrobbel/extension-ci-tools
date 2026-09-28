# Extension CI Tools for DuckDB

This repository contains reusable components for building, testing and deploying DuckDB extensions.

DuckDB's [Extension Template](https://github.com/duckdb/extension-template) and various DuckDB extensions use this repository to share build configuration and CI workflows.

## Usage examples

Add this repository to an extension as the `extension-ci-tools` submodule. After cloning the extension, initialize its submodules:

```shell
git submodule update --init --recursive
```

When the extension has a `duckdb` submodule, omit `duckdb_version` from the distribution and deployment workflows. CI uses the commit pinned by the submodule.

### C++

[duckdb-httpfs](https://github.com/duckdb/duckdb-httpfs/) includes the standard extension and vcpkg makefiles:

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXT_NAME=httpfs
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

include extension-ci-tools/makefiles/duckdb_extension.Makefile
include extension-ci-tools/makefiles/vcpkg.Makefile
```

It calls the reusable distribution workflow to build and test the extension on supported platforms:

```yaml
jobs:
  duckdb-stable-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: httpfs
      ci_tools_version: main
```

### Rust

[duckdb-delta](https://github.com/duckdb/duckdb-delta/) uses the standard extension makefile and enables the Rust toolchain in CI:

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXT_NAME=delta
EXT_CONFIG=${PROJ_DIR}extension_config.cmake

include extension-ci-tools/makefiles/duckdb_extension.Makefile
```

```yaml
jobs:
  duckdb-stable-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: delta
      ci_tools_version: main
      enable_rust: true
```

### C API

[odbc-scanner](https://github.com/duckdb/odbc-scanner/) does not have a `duckdb` submodule, so it sets the DuckDB version explicitly. It uses the C API makefiles. Set `USE_UNSTABLE_C_API` to `1` only when the extension needs DuckDB's unstable C API.

```make
PROJ_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

EXTENSION_NAME := odbc_scanner
USE_UNSTABLE_C_API := 0
TARGET_DUCKDB_VERSION := <duckdb-version>

include extension-ci-tools/makefiles/c_api_extensions/base.Makefile
include extension-ci-tools/makefiles/c_api_extensions/c_cpp.Makefile
```

Its CI adds the tools needed by the extension:

```yaml
jobs:
  duckdb-build:
    uses: duckdb/extension-ci-tools/.github/workflows/_extension_distribution.yml@main
    with:
      extension_name: odbc_scanner
      duckdb_version: <duckdb-version>
      ci_tools_version: main
      extra_toolchains: python3;unixodbc;
      build_duckdb_shell: false
```

The shared makefiles provide common targets such as `make debug`, `make test_debug` and `make release`.

## Extensions in a subdirectory

`build.yml`, `_extension_distribution.yml`, and `_extension_code_quality.yml`
accept `extension_directory`, relative to the extension repository root. The default
is `.`, preserving the root-level layout. For example, the grouped build workflow
can build an extension under `integrations/duckdb`:

```yaml
jobs:
  extension:
    uses: duckdb/extension-ci-tools/.github/workflows/build.yml@main
    with:
      duckdb_version: <duckdb-version>
      ci_tools_version: main
      extension_directory: integrations/duckdb
      groups: |
        extension:
          config: ci/extensions.cmake
          toolchain: main
```

The selected directory contains the extension's Makefile and build configuration.
DuckDB and `extension-ci-tools` are checked out below it. Group configuration paths
(in this example, `integrations/duckdb/ci/extensions.cmake`) and vcpkg overlay paths
are relative to that directory. Builds, tests, and quality checks run there;
build outputs and Rust logs are collected from there as well.

The full repository stays available, including parent sources and Git metadata in
Linux containers. Compiler caches, prebuilt DuckDB downloads, and staged artifacts
remain at the repository root. Separate v2 test jobs restore their test binaries
and extension artifacts into the selected directory's build tree.

When overriding the extension repository, `extension_directory` is relative to
that checkout. Use the same value in the code-quality workflow. Deployment and
client-test workflows are unchanged.

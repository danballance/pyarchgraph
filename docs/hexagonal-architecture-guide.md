# A shared Python layout for hexagonal architecture

Use the same architectural vocabulary across projects, with components chosen
for each project's needs. This guide refines the supplied version 2 specification:
it permits collaboration within a layer and adapter component, makes model
validation depend on lifecycle, and does not require unused infrastructure.

## Layout and conventions

The importable application package sits directly in the repository root. This is
the **flat packaging layout**; modules inside that package can be nested.

```text
project/
├── my_app/
│   ├── __init__.py              # Documentation and optional version metadata
│   ├── __main__.py              # Thin executable bootstrap, when needed
│   ├── main.py                  # Concrete wiring and application factory
│   ├── domain/
│   │   ├── models.py            # Domain values and entities
│   │   └── ...                  # Policies, algorithms and domain-owned ports
│   ├── application/
│   │   ├── requests.py          # Use-case inputs
│   │   ├── results.py           # Use-case outputs
│   │   ├── exceptions.py        # Application boundary failures
│   │   ├── ports/              # Application-owned input/output contracts
│   │   └── use_cases/          # Workflows and orchestration
│   └── adapters/
│       ├── driving/            # CLI, HTTP, event consumers, as needed
│       └── driven/             # Filesystem, database, remote APIs, as needed
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── acceptance/
│   └── architecture/
├── docs/
├── AGENTS.md
├── README.md
└── pyproject.toml
```

Flat packaging and class-based runtime behaviour are chosen team conventions,
not requirements of hexagonal architecture or of Python. Put behaviour in
methods and keep state explicit through constructor injection. A thin `main`
function may delegate the executable bootstrap to the application factory.

Use absolute imports from the module that defines a value or service. Package
initializers contain documentation and optional version metadata; avoid eager
re-exports and registration side effects. Importing a core module must not load
concrete adapters or their dependencies.

The shared structure establishes ownership. Create additional modules only when
they contain useful concepts. A project without persistence needs no repository
package; a project without notifications needs no notification port. Use
`requests.py` for simple workflows, or commands and queries when that distinction
serves the application. Do not create empty frameworks of placeholders.

## Dependencies and ownership

| Layer | Responsibilities | Allowed dependencies |
| --- | --- | --- |
| `domain` | Domain values, invariants, policies and algorithms | Standard library and other domain modules |
| `application` | Use-case requests/results, workflow orchestration, application ports | Standard library, domain and other application modules |
| `adapters` | Translate external inputs/outputs and implement technical capabilities | Core, external libraries and helpers within the same adapter component |
| `main` | Instantiate and connect concrete implementations | All runtime layers |
| `__main__` | Delegate process startup | Composition root and necessary standard-library bootstrap types |

Core modules must not read files, inspect the process environment, start
processes, access the network or write to process streams. Pure path values and
standard-library calculations are fine. A standard-library import alone does
not prove purity; check the operations used as well.

A **driving adapter** translates an external request and invokes an application
input port. A **driven adapter** implements a capability consumed by the core.
Adapters may call helper modules inside their own component. For example,
`driving/cli/application.py` may use `driving/cli/rendering.py`, and a persistence
repository may use ORM models from its own persistence package. Separate
components remain independent; coordinate them through application logic and
ports, with the composition root supplying implementations.

Own a port in the layer whose logic consumes the abstraction. A pure graph
algorithm used by a domain strategy belongs to the domain. A source-discovery
capability used by an analysis workflow belongs to the application. Repositories
are one kind of port, not a mandatory category in every project.

Prefer `typing.Protocol` for small contracts named after intent, without vendor
names. Built-in implementations may explicitly inherit protocols to make their
contracts visible; external implementations can use structural typing.
Use manual dependency injection until a project has a concrete need for a
container. Do not make the core look up its own concrete dependencies.

## Values, lifecycle and validation

Represent stable values with frozen dataclasses and immutable collections where
practical. Separate raw external inputs, intermediate observations, and validated
domain values instead of representing an unfinished value with fake identifiers.

Enforce local invariants when the object is constructed or through its explicit
factory. Validate contextual invariants at the boundary that has the required
information: uniqueness needs a collection; a selected strategy needs a registry;
provenance needs the original snapshot. An immutable object alone does not prove
that these contextual requirements hold.

Application requests may carry unvalidated input until the use-case boundary,
which validates it before performing external I/O. Adapters translate transport
formats; domain policies enforce domain rules; application results describe the
outcome independently of transport-specific status codes and presentation.

When persistence is required, keep ORM models in the persistence adapter and map
them to domain values. Domain models must not carry ORM annotations or framework
decorators. Apply the same separation to HTTP schemas and third-party SDK types.

## Enforcement

Use import-linter for recursive dependency graphs and pytest for conventions and
purity checks. Include type-checking imports: a dependency hidden behind
`TYPE_CHECKING` still affects ownership. Enable external packages explicitly when
forbidding framework imports. Example configuration:

```toml
[tool.importlinter]
root_package = "my_app"
include_external_packages = true
exclude_type_checking_imports = false

[[tool.importlinter.contracts]]
name = "Inward dependencies"
type = "layers"
containers = ["my_app"]
layers = ["__main__", "main", "adapters", "application", "domain"]
exhaustive = true

[[tool.importlinter.contracts]]
name = "Adapter directions are independent"
type = "independence"
modules = ["my_app.adapters.driving", "my_app.adapters.driven"]
```

Tailor the contract to the modules that exist. Add independence contracts between
actual adapter components and forbidden contracts for actual external dependencies.
Do not forbid imports between helper modules inside one component. The
[Import Linter configuration reference](https://import-linter.readthedocs.io/en/stable/get_started/configure/)
and [contract reference](https://import-linter.readthedocs.io/en/stable/contract_types/)
describe the configuration options.

Classify files recursively by their package-relative path, not their immediate
parent directory. Supplement import checks with standard-library-only core
checks, known external-system operation checks and cold imports with external
dependencies blocked. Test the enforcement with small invalid nested packages,
including type-checking imports; a passing check on a compliant repository alone
does not demonstrate that forbidden dependencies are detected.

## Testing and adoption

Unit tests exercise pure behaviour and injected collaborators; a pure adapter can
have unit tests. Integration tests exercise real filesystem, database or library
collaboration. Acceptance tests exercise complete user-facing CLI/API scenarios.
Architecture tests enforce structural rules across all runtime packages.

For a restructuring, capture observable results before moving code. Preserve
serialization, identifiers, ordering and errors unless explicitly changing their
contracts. Test installed wheels and source distributions outside the checkout,
because flat layouts can conceal missing package files during local tests.
Document Python import/API migrations separately from any external format change.

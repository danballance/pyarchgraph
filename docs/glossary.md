# PyArchGraph glossary

The vocabulary used in the code and package guides. Code names are shown beside
the concepts they represent; prose uses **catalogue**, while Python identifiers
retain **Catalog**.

Read this glossary in the [HTML view](glossary.html).

Read the package guides: [Domain](domain.html) · [Application](application.html) ·
[Adapters](adapters.html). For package boundaries, see [Architecture](architecture.md).

- [Source scope and identity](#source-scope-and-identity)
- [Import observations and resolution](#import-observations-and-resolution)
- [Views and checks](#views-and-checks)
- [Workflow and reports](#workflow-and-reports)
- [Architecture vocabulary](#architecture-vocabulary)

## Source scope and identity

### Source scope

The files selected for analysis by source roots and exclusions. This is distinct
from an import's **context**, which describes where a statement appears inside a file.

### Source root

A directory from which dotted import names are interpreted, like a directory on
Python's `sys.path`. Several roots can be analysed together. Root order does not
decide which conflicting module wins; a nested root owns its own files.

### Base directory

The reference directory for relative roots and paths in the report. It makes
locations from different source roots comparable. Set through `AnalysisRequest.base_dir`.

### Source module

One inventoried Python source file, including package initialisers and files
without usable dotted import names. `SourceModule` records its identity, path,
binding status, and analysis status.

### Source ID

The identity of a source file in the analysis: `source:` followed by its path
relative to the base directory, such as `source:src/orders.py`. It identifies a
file, independently of the name used to import it.

### Import name

A dotted name such as `shop.orders` used to refer to a module or package. A source
file may have an import name, or may be analysed by path alone.

### Package and namespace

A regular package has an `__init__.py` source file. A namespace groups import
names without that initialiser. Finding `google.service` does not imply that
PyArchGraph owns every sibling under `google`.

### Binding

The association between an import name and a source file. A source can be
**bound**, **path-only** (`path_only`), **shadowed** by a preferred package, or
**ambiguous** because competing sources prevent a clear choice. Shadowed and
path-only files still contribute imports that can be analysed.

### Ownership

The names treated as belonging to the analysed project. Selected source names,
regular packages, and explicit `owned_prefixes` establish ownership; namespace
ancestors alone do not. Ownership helps distinguish a missing internal target
from an import outside the project.

### Exclusion

A file or directory intentionally omitted from collection. `ExcludedPath`
records the path and the rule responsible. The application excludes tests by
default; filesystem discovery also skips common generated and environment directories.

### Target declaration

A description of a target whose implementation is outside the analysed Python
sources. `TargetDeclaration` can describe a stub, native implementation, generated
module, or discovered exclusion. Declarations may come from discovery or explicit
configuration; configuration supports stub, native, and generated targets.

### Target boundary

A recorded import of a declared target whose implementation was not analysed.
`TargetBoundary` includes the declaration and evidence of its use. A declaration
alone does not mean a boundary was encountered.

### Acknowledgement

Acceptance of a target boundary's missing implementation coverage. Configuration
can grant it explicitly; ordinary exclusions receive it automatically.
Acknowledgement remains visible in the report. It does not analyse the target,
add dependencies through it, or excuse errors in selected source files.

## Import observations and resolution

### Explicit import

An `import` or `from ... import ...` statement visible in source code.
`ImportSyntax` distinguishes the two forms. Calls such as
`importlib.import_module(...)` are dynamic imports and are outside this analysis.

### Import site

The location of an import statement in a source file. One site can produce several
facts: `import orders, inventory` contains two imported items.

### Import context

Where an import appears: for example, inside a function, a class body, a typing
guard, or a conditional block. `ImportContext` records these observations so views
can select imports. Context does not predict when code will execute.

### Import fact draft

One imported item observed in source code, before it receives a stable fact ID.
`ImportFactDraft` holds its spelling, location, and context. Catalogue assembly
first puts its source identity and path into the report's coordinate system.

### Import fact

A recorded imported item with a stable identity, location, and context.
`ImportFact` describes what the source says; it does not by itself say what the
import resolves to. “Source fact” in conversation refers to this import fact.

### Canonicalisation

Putting collected drafts into a consistent order and assigning content-derived
fact IDs. `FactCanonicalizer` makes identical inputs produce identical identities.
An edit to an identity field, including location, can change the ID.

### Discovery result and fact collection

The observations returned by the collection ports. `DiscoveryResult` contains
inventoried sources, names, declarations, exclusions, and diagnostics.
`FactCollection` contains import fact **drafts** and collection diagnostics.

### Catalogue

The application’s combined inventory for all selected roots. `SourceCatalog`
brings together sources, final import facts, target declarations, exclusions, and
diagnostics. `SourceCatalogBuilder` assembles it before import resolution.

### Import resolver

The service that interprets import facts against known source bindings, ownership,
and target declarations. `ImportResolver` is its contract; `StaticImportResolver`
is the built-in implementation. It does not import project code or inspect installed packages.

### Resolution result

The outcome of interpreting import facts: source dependencies, external imports,
unresolved imports, encountered boundaries, and diagnostics. Represented by
`ResolutionResult`.

### Resolution kind

How evidence links an import to a source. `ResolutionKind` distinguishes an exact
module, an exact base in a `from` statement, and a probable submodule. A probable
submodule may instead be a package attribute; static analysis cannot always decide.

### Dependency

A directed relationship from an importing source to an imported source, supported
by import facts. `DependencyEdge` groups evidence for one source pair. Several
facts can support the same dependency.

### Evidence

The connection from an analysis result back to the source statements supporting
it. `DependencyEvidence` records a fact ID and resolution kind; `ViewEvidence`
also preserves the original source endpoints when a view groups sources.

### Evidence location

A readable source reference attached to a result: path, position, statement text,
and context. `EvidenceLocation` uses base-directory-relative paths and one-based
character positions in reports.

### External import

An import classified outside the analysed source graph. `ExternalImport`
distinguishes standard-library names from other external names. This classification
does not establish that a dependency is installed or can be imported successfully.

### Unresolved import

An import the resolver cannot connect to a known source or declared target.
`UnresolvedImport` records the reason, such as a missing internal
target, ambiguous binding, or unknown package context. Checks decide which records
become findings; coverage diagnostics can also report resolution problems.

### Reconciliation

Making separately collected information agree, or reporting conflicts.
`BindingReconciler` checks bindings across roots; `TargetReconciler` combines
discovered and configured target declarations and checks their package layout.

## Views and checks

### Analysis snapshot

The shared source analysis supplied to every view: sources, facts, selected
dependencies, external imports, and unresolved imports. `AnalysisSnapshot` is
prepared before views filter or group that information.

### View

A chosen perspective on the snapshot. A `GraphViewStrategy` produces a `ViewGraph`
by selecting imports or grouping sources. Built-in views keep one node per source;
extensions may group sources into larger units.

### Structural view

The built-in view retaining all explicit import facts. `StructuralView` is
registered as `structural` and is the default gate.

### Non-typing view

The built-in view omitting imports inside recognised `TYPE_CHECKING` bodies.
`NonTypingView` is registered as `non-typing`. Recognition is conservative;
unrecognised or ambiguous conditions remain included.

### Module-body view

The built-in view omitting recognised typing-only imports and imports inside
functions or methods. `ModuleBodyView` is registered as `module-body`; class bodies
outside functions remain included. It filters syntax rather than simulating startup execution.

### Projection

Turning source dependencies into the nodes and edges of a view. A projection may
filter evidence or combine sources, while preserving the original evidence behind
each retained relationship. `SourceGraphProjection` builds the built-in source views.

### View node and membership

A node is one unit shown in a view. `ViewNode` has an ID, label, and member source
IDs. Membership says which original sources that node represents; sources cannot
belong to overlapping nodes in the same view.

### View edge

A directed relationship between two view nodes. `ViewEdge` retains evidence from
the original source dependencies, even when its endpoints represent groups of files.

### Cycle and cyclic component

A cycle follows dependencies back to its starting node. A cyclic component is a
group whose nodes can all reach one another, including a single node importing
itself. A `CycleFinding` describes one such group, which may contain many cycles.

### Witness

One reproducibly chosen closed path demonstrating a cycle. The witness in a
`CycleFinding` gives a manageable example with evidence; it does not enumerate
every cycle or necessarily visit every member of the component.

### Cycle certainty

**Definite** means the component contains a cycle supported by exact resolution;
`definite_members` identifies nodes participating in such cycles. **Possible**
means a cycle depends on probable resolution. Neither label predicts a runtime crash.

### Check and check context

A rule applied to a view. `CheckStrategy` receives a `CheckContext` containing the
view, retained facts and imports, sources, and cycle analysis, then returns
`CheckResult` values. Built-ins report cycles and unresolved imports.

### Finding

Something a check wants the user to review. A finding can describe a cycle
(`CycleFinding`), an unresolved import (`ImportFinding`), or an extension's rule
(`RuleFinding`). `CheckResult` pairs it with a severity; `RegisteredFinding` adds
the responsible check's ID.

### Diagnostic

A message about collection, scope, resolution, or coverage, carried by
`Diagnostic`. It belongs to coverage rather than an optional view check. A
resolution problem may produce both a diagnostic and a finding.

### Severity

The importance of a diagnostic or finding: error, warning, or information.
`Severity` supplies these labels. Error diagnostics affect completeness; error
findings in the gate affect the CLI result when analysis is complete.

## Workflow and reports

### Analysis request and options

The input to one analysis. `AnalysisRequest` carries source roots, a base directory,
and `AnalysisOptions`. Options choose exclusions, a gate, detail level, ownership
prefixes, and target declarations. The application validates them before collection.

### Project observations

Location and packaging information obtained through `ProjectAccess`.
`ProjectLocation` describes the base directory and roots; `ProjectMetadata`
contains possible roots and package aliases; `DirectoryLocation` records a
directory's location and whether it exists.

### Strategy

A replaceable way to build a view or perform a check. The domain defines the
contracts; the application arranges when registered strategies run and validates
their results.

### Registration and registry

A registration gives a strategy a name and, for checks, optional view applicability.
`ViewRegistration` and `CheckRegistration` live in `StrategyRegistry`, which also
records which checks are selected for each view. `StrategyEngine` runs them.

### Coverage

The account of what analysis examined and where its limits are. `Coverage`
records roots, exclusions, analysed source count, diagnostics, target boundaries,
and stated limitations. `CoveragePolicy` decides whether those limits allow a
complete result.

### Complete and incomplete

An analysis is **complete** within its declared scope when there are no error
diagnostics and every encountered target boundary is acknowledged. It can still
contain error findings. **Incomplete** means coverage is insufficient; useful
partial results may still be returned.

### View report

The completed result for one view: nodes, dependency and cycle counts, enabled
checks, and registered findings. `ViewReport` is an application result, distinct
from the intermediate `ViewGraph` used to run checks.

### Analysis report

The assembled result of one analysis. `AnalysisReport` includes completion status,
sources, coverage, a gate, and the reports for all registered views. JSON is one
presentation of this result, produced by an adapter.

### Gate

The view selected to determine the CLI's finding-based exit status. Other views
remain in the report. Gate selection does not change coverage requirements.
Available as `AnalysisReport.gate` and the `selected_view` convenience property.

### Detail level

How much cycle evidence is included. `summary` supplies a witness; `component-edges`
also lists every dependency inside the cyclic component. The `Details` type names
these choices. Neither option enumerates every elementary cycle.

### Analysis failure and extension failure

`AnalysisError` describes an invalid analysis request or setup. `ExtensionError`
describes a registered strategy failing or returning an invalid result. These
prevent a report; source collection failures can instead produce a partial report.

### Exit code

The CLI's process result, chosen by `CliExitCodePolicy`: **0** for complete analysis
with no gate error findings, **1** for complete analysis with gate error findings,
and **2** for incomplete analysis. Invocation and extension failures also return
2 through CLI error handling.

## Architecture vocabulary

### Domain

The concepts and rules that give collected source information meaning: import
resolution, dependencies, views, checks, and coverage policy. The `domain` package
does not read files or depend on NetworkX.

### Application and use case

The application organises the work needed to answer a request. Its analysis use
case, `AnalyseProject`, coordinates collection and domain rules, then assembles a
report. The public analysis contract is `ProjectAnalyzer.analyse(request)`.

### Port and protocol

A port is a contract for a capability needed or offered by the core. Python
`Protocol` classes describe these contracts. For example, the application needs
`SourceDiscovery`, while the domain needs `GraphAlgorithms`.

### Adapter

A component connecting core contracts to a particular interface or technology.
A **driving** adapter, such as the CLI, starts analysis. **Driven** adapters provide
capabilities such as filesystem access, Python parsing, or NetworkX graph operations.

### Syntax tree

A structured description of Python source, often called an AST. `PythonAstParser`
creates it; `ModuleImportExtractor` uses it to observe import statements and their
context. Parsing does not execute the source.

### Graph algorithms and graph handle

The graph operations the domain needs to identify cyclic groups and obtain
witnesses. `GraphAlgorithms` prepares a graph; `GraphHandle` provides queries on
that prepared graph. The NetworkX adapter implements both contracts.

### Composition root

The place that chooses concrete implementations and connects them.
`main.ApplicationFactory` supplies adapters and policies to the application.
`__main__.main` is the small process entry point that starts the CLI.

### Validation

Checking that information satisfies the rules at the point where enough context
is available. Request validation precedes collection; snapshot, view, and finding
validation protect the shared analysis and extension boundaries.
